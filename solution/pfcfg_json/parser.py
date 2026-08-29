"""Parse .pfcfg files into the pfcfg-json/v1 AST.

Interpolation is parsed into Value nodes, not expanded. Conditional
activity is decided from the provided environment (process env by
default) as each @ifdef/@ifndef is encountered. Inactive bodies are
consumed so @endif matches, but they do not contribute statements.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from .model import (
    Concat,
    Document,
    Env,
    EnvAlternate,
    EnvDefault,
    Ifdef,
    Ifndef,
    Include,
    IncludeOnce,
    Literal,
    Ref,
    Set,
    Stmt,
    Value,
)

_DIRECTIVES = frozenset(
    {"include", "include_once", "ifdef", "ifndef", "endif"}
)


class ParseError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        source: str | None = None,
        line: int | None = None,
    ) -> None:
        self.source = source
        self.line = line
        loc = []
        if source:
            loc.append(source)
        if line is not None:
            loc.append(f"line {line}")
        prefix = f"{':'.join(loc)}: " if loc else ""
        super().__init__(prefix + message)


def env_is_set(env: Mapping[str, str], var: str) -> bool:
    value = env.get(var)
    return value is not None and value != ""


def parse_file(
    path: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    resolve_conditionals: bool = True,
) -> Document:
    """Parse a single file. Does not load included files."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    return parse_text(
        text,
        source=_source_name(path),
        env=os.environ if env is None else env,
        resolve_conditionals=resolve_conditionals,
    )


def parse_text(
    text: str,
    *,
    source: str = "<string>",
    env: Mapping[str, str] | None = None,
    resolve_conditionals: bool = True,
) -> Document:
    parser = _Parser(
        text,
        source=source,
        env=os.environ if env is None else env,
        resolve_conditionals=resolve_conditionals,
    )
    return parser.parse_document()


def parse_entry(
    path: str | Path,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Document]:
    """Parse an entry file and every actively included file.

    Detects include cycles. ``include_once`` still loads a first occurrence
    (skipping is an evaluation concern).
    """
    env_map: Mapping[str, str] = os.environ if env is None else env
    docs: dict[str, Document] = {}

    def rec(file_path: Path, stack: tuple[str, ...]) -> None:
        canonical = str(file_path.resolve())
        if canonical in stack:
            chain = " -> ".join((*stack, canonical))
            raise ParseError(f"include cycle: {chain}", source=canonical)
        if canonical in docs:
            return
        if not file_path.is_file():
            raise ParseError(
                f"included file not found: {file_path}",
                source=stack[-1] if stack else canonical,
            )
        doc = parse_file(file_path, env=env_map)
        docs[canonical] = doc
        new_stack = (*stack, canonical)
        for rel in _active_include_paths(doc.body):
            rec((file_path.parent / rel).resolve(), new_stack)

    rec(Path(path), ())
    return docs


def parse_value(text: str, *, source: str | None = None, line: int | None = None) -> Value:
    try:
        parts, index = _parse_concat(text, 0, stop="")
    except ParseError as exc:
        raise ParseError(str(exc), source=source, line=line) from None
    if index != len(text):
        raise ParseError(
            f"unexpected character {text[index]!r} in value",
            source=source,
            line=line,
        )
    return _glue(parts)


# --- line parser ------------------------------------------------------------


class _Parser:
    def __init__(
        self,
        text: str,
        *,
        source: str,
        env: Mapping[str, str],
        resolve_conditionals: bool = True,
    ) -> None:
        self.source = source
        self.env = env
        self.resolve_conditionals = resolve_conditionals
        self.lines = _split_lines(text)
        self.i = 0
        self.seen_section = False
        self.section: str | None = None

    def error(self, message: str, line: int | None = None) -> ParseError:
        if line is None and self.i < len(self.lines):
            line = self.lines[self.i][0]
        elif line is None and self.lines:
            line = self.lines[-1][0]
        return ParseError(message, source=self.source, line=line)

    def parse_document(self) -> Document:
        body = self._parse_stmts(active=True, stop_endif=False)
        if self.i < len(self.lines):
            raise self.error("unexpected content after end of file")
        return Document(source=self.source, body=tuple(body))

    def _parse_stmts(self, *, active: bool, stop_endif: bool) -> list[Stmt]:
        stmts: list[Stmt] = []
        while self.i < len(self.lines):
            lineno, raw = self.lines[self.i]
            stripped = raw.strip()
            if not stripped or stripped[0] in "#;":
                self.i += 1
                continue

            if stripped.startswith("@"):
                op, rest = self._directive(stripped, lineno)
                if op == "endif":
                    if rest:
                        raise self.error("@endif does not take arguments", lineno)
                    if not stop_endif:
                        raise self.error("unmatched @endif", lineno)
                    return stmts
                if op in ("ifdef", "ifndef"):
                    self.i += 1
                    var = rest.strip()
                    if not var or not _is_ident(var):
                        raise self.error(f"@{op} requires an environment variable name", lineno)

                    if self.resolve_conditionals:
                        taken = env_is_set(self.env, var)
                        inner_active = active and (taken if op == "ifdef" else not taken)
                    else:
                        # Preserve conditional bodies for conversion.
                        inner_active = active

                    body = self._parse_conditional_body(inner_active)
                    if active:
                        node: Stmt = (
                            Ifdef(var, tuple(body))
                            if op == "ifdef"
                            else Ifndef(var, tuple(body))
                        )
                        stmts.append(node)
                    continue
                if op in ("include", "include_once"):
                    self.i += 1
                    if not active:
                        continue
                    if self.seen_section:
                        raise self.error(
                            "include directives must appear before any section headers in that file",
                            lineno,
                        )
                    inc_path = rest.strip()
                    if not inc_path:
                        raise self.error(f"@{op} requires a path", lineno)
                    if inc_path.startswith(("'", '"')):
                        inc_path, _trailing = _read_quoted(inc_path)
                    stmts.append(
                        Include(inc_path) if op == "include" else IncludeOnce(inc_path)
                    )
                    continue
                raise self.error(f"unknown directive @{op}", lineno)

            if stripped.startswith("["):
                self.i += 1
                if active:
                    self.section = self._section_name(stripped, lineno)
                    self.seen_section = True
                continue

            self.i += 1
            if not active:
                continue
            if self.section is None:
                raise self.error("assignment without a section header", lineno)
            key, value = self._assignment(raw, lineno)
            stmts.append(Set(self.section, key, value))
        if stop_endif:
            raise self.error("unclosed conditional: missing @endif")
        return stmts

    def _parse_conditional_body(self, inner_active: bool) -> list[Stmt]:
        body = self._parse_stmts(active=inner_active, stop_endif=True)
        if self.i >= len(self.lines):
            raise self.error("unclosed conditional: missing @endif")
        lineno, raw = self.lines[self.i]
        op, rest = self._directive(raw.strip(), lineno)
        if op != "endif" or rest:
            raise self.error("expected @endif", lineno)
        self.i += 1
        return body

    def _directive(self, stripped: str, lineno: int) -> tuple[str, str]:
        line = _strip_unquoted_comment(stripped).strip()
        parts = line.split(None, 1)
        token = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        if not token.startswith("@"):
            raise self.error("internal: expected directive", lineno)
        op = token[1:]
        if op not in _DIRECTIVES:
            raise self.error(f"unknown directive @{op}", lineno)
        return op, rest

    def _section_name(self, stripped: str, lineno: int) -> str:
        line = _strip_unquoted_comment(stripped).strip()
        if not line.startswith("[") or "]" not in line:
            raise self.error("malformed section header", lineno)
        inner, after = line[1:].split("]", 1)
        if after.strip():
            raise self.error("trailing content after section header", lineno)
        name = inner.strip()
        if not name:
            raise self.error("empty section name", lineno)
        return name

    def _assignment(self, raw: str, lineno: int) -> tuple[str, Value]:
        line = raw.strip()
        eq = _find_unquoted(line, "=")
        if eq < 0:
            raise self.error("expected key = value", lineno)
        key = line[:eq].strip()
        rhs = line[eq + 1 :].lstrip()
        if not key or not _is_key(key):
            raise self.error(f"malformed key {key!r}", lineno)
        if rhs.startswith('"'):
            text, rest = _read_quoted(rhs)
            rest = _strip_unquoted_comment(rest).strip()
            if rest:
                raise self.error("trailing content after quoted value", lineno)
        else:
            text = _strip_unquoted_comment(rhs).strip()
        return key, parse_value(text, source=self.source, line=lineno)


def _split_lines(text: str) -> list[tuple[int, str]]:
    return [(i, line) for i, line in enumerate(text.splitlines(), start=1)]


def _source_name(path: Path) -> str:
    return path.as_posix()


def _is_ident(name: str) -> bool:
    return name.replace("_", "a").isalnum() and (name[0].isalpha() or name[0] == "_")


def _is_key(name: str) -> bool:
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in name)


def _strip_unquoted_comment(text: str) -> str:
    cut = _find_unquoted(text, "#")
    semi = _find_unquoted(text, ";")
    positions = [p for p in (cut, semi) if p >= 0]
    if not positions:
        return text
    return text[: min(positions)]


def _find_unquoted(text: str, char: str) -> int:
    i = 0
    in_quote = False
    escape = False
    interpolation_depth = 0

    while i < len(text):
        c = text[i]

        if in_quote:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_quote = False
            i += 1
            continue

        if c == '"':
            in_quote = True
            i += 1
            continue

        # ${...} interpolation: comment characters inside it are literal.
        if c == "$" and i + 1 < len(text) and text[i + 1] == "{":
            interpolation_depth += 1
            i += 2
            continue

        if interpolation_depth:
            if c == "}":
                interpolation_depth -= 1
            i += 1
            continue

        if c == char:
            return i

        i += 1

    return -1

def _read_quoted(text: str) -> tuple[str, str]:
    if not text.startswith('"'):
        raise ParseError("expected quoted string")
    out: list[str] = []
    i = 1
    while i < len(text):
        c = text[i]
        if c == "\\":
            if i + 1 >= len(text):
                raise ParseError("unterminated escape in quoted value")
            nxt = text[i + 1]
            if nxt in '"\\':
                out.append(nxt)
            else:
                raise ParseError(f"invalid escape \\{nxt}")
            i += 2
            continue
        if c == '"':
            return "".join(out), text[i + 1 :]
        out.append(c)
        i += 1
    raise ParseError("unterminated quoted value")


def _active_include_paths(body: tuple[Stmt, ...]) -> list[str]:
    paths: list[str] = []
    for stmt in body:
        if isinstance(stmt, (Include, IncludeOnce)):
            paths.append(stmt.path)
        elif isinstance(stmt, (Ifdef, Ifndef)):
            paths.extend(_active_include_paths(stmt.body))
    return paths


# --- interpolation ----------------------------------------------------------


def _glue(parts: list[Value]) -> Value:
    merged: list[Value] = []
    for part in parts:
        if merged and isinstance(merged[-1], Literal) and isinstance(part, Literal):
            merged[-1] = Literal(merged[-1].text + part.text)
        else:
            merged.append(part)
    if not merged:
        return Literal("")
    if len(merged) == 1:
        return merged[0]
    return Concat(tuple(merged))


def _parse_concat(text: str, i: int, stop: str) -> tuple[list[Value], int]:
    parts: list[Value] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            parts.append(Literal("".join(buf)))
            buf.clear()

    n = len(text)
    while i < n:
        c = text[i]
        if c in stop:
            break
        if c == "$" and i + 1 < n and text[i + 1] == "{":
            flush()
            node, i = _parse_env(text, i)
            parts.append(node)
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            flush()
            node, i = _parse_ref(text, i)
            parts.append(node)
            continue
        buf.append(c)
        i += 1
    flush()
    return parts, i


def _parse_env(text: str, i: int) -> tuple[Value, int]:
    # ${
    i += 2
    start = i
    if i >= len(text) or not (text[i].isalpha() or text[i] == "_"):
        raise ParseError("malformed ${...}: expected variable name")
    i += 1
    while i < len(text) and (text[i].isalnum() or text[i] == "_"):
        i += 1
    var = text[start:i]
    if i < len(text) and text.startswith(":-", i):
        inner, i = _parse_concat(text, i + 2, stop="}")
        i = _expect_close(text, i, "}")
        return EnvDefault(var, _glue(inner)), i
    if i < len(text) and text.startswith(":+", i):
        inner, i = _parse_concat(text, i + 2, stop="}")
        i = _expect_close(text, i, "}")
        return EnvAlternate(var, _glue(inner)), i
    if i < len(text) and text[i] == "}":
        return Env(var), i + 1
    raise ParseError(f"malformed ${{{var}...}}")


def _parse_ref(text: str, i: int) -> tuple[Ref, int]:
    # $(
    i += 2
    start = i
    while i < len(text) and text[i] != ")":
        i += 1
    if i >= len(text):
        raise ParseError("unclosed $(...)")
    inner = text[start:i]
    i += 1
    if not inner or "." not in inner:
        raise ParseError(f"malformed reference $({inner})")
    section, _, key = inner.rpartition(".")
    if not section or not key:
        raise ParseError(f"malformed reference $({inner})")
    if any(ch.isspace() for ch in inner):
        raise ParseError(f"malformed reference $({inner})")
    return Ref(section, key), i


def _expect_close(text: str, i: int, char: str) -> int:
    if i >= len(text) or text[i] != char:
        raise ParseError(f"unclosed interpolation, expected {char!r}")
    return i + 1
