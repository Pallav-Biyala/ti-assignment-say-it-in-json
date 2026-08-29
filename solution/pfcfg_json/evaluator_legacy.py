"""Reference evaluator for legacy .pfcfg files.

Computes effective settings for a given process environment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticReport,
    Severity,
)
from .model import (
    Concat,
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
from .parser import ParseError, env_is_set, parse_entry, parse_file

DEFAULT_EXPANSION_PASS_LIMIT = 100


class EvalError(Exception):
    def __init__(
        self,
        message: str,
        *,
        file: str | None = None,
        section: str | None = None,
        key: str | None = None,
    ) -> None:
        self.file = file
        self.section = section
        self.key = key
        super().__init__(message)


@dataclass(frozen=True)
class EffectiveConfig:
    values: dict[tuple[str, str], str]
    unresolved: dict[tuple[str, str], str]
    diagnostics: DiagnosticReport

    def sections(self) -> list[str]:
        sects: set[str] = set()
        for s, _k in self.values:
            sects.add(s)
        for s, _k in self.unresolved:
            sects.add(s)
        return sorted(sects)

    def keys_in_section(self, section: str) -> list[str]:
        ks: set[str] = set()
        for s, k in self.values:
            if s == section:
                ks.add(k)
        for s, k in self.unresolved:
            if s == section:
                ks.add(k)
        return sorted(ks)

    def get(self, section: str, key: str) -> tuple[str, bool]:
        k = (section, key)
        if k in self.values:
            return self.values[k], True
        if k in self.unresolved:
            return self.unresolved[k], False
        return "", False

    def to_flat_dict(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for (s, k), v in self.values.items():
            result[f"{s}.{k}"] = v
        return result

    def to_nested_dict(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for (s, k), v in self.values.items():
            result.setdefault(s, {})[k] = v
        return result


def evaluate_pfcfg_entry(
    entry_path: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    pass_limit: int = DEFAULT_EXPANSION_PASS_LIMIT,
) -> EffectiveConfig:
    env_map: Mapping[str, str] = os.environ if env is None else env
    report = DiagnosticReport()
    entry = Path(entry_path)

    try:
        docs = parse_entry(entry, env=env_map)
    except ParseError as exc:
        report.add(Diagnostic(
            code=DiagnosticCode.PARSE_ERROR,
            severity=Severity.ERROR,
            reason=str(exc),
            file=getattr(exc, "source", None),
            line=getattr(exc, "line", None),
        ))
        return EffectiveConfig(values={}, unresolved={}, diagnostics=report)

    flat: dict[tuple[str, str], Value] = {}
    seen_includes: set[str] = set()

    def process_body(
        stmts: tuple[Stmt, ...],
        *,
        file_dir: Path,
        file_path: str,
        active: bool,
    ) -> None:
        for stmt in stmts:
            if isinstance(stmt, Include):
                if not active:
                    continue
                target = (file_dir / stmt.path).resolve()
                canonical = str(target)
                if not target.is_file():
                    report.add(Diagnostic(
                        code=DiagnosticCode.INCLUDE_MISSING,
                        severity=Severity.ERROR,
                        reason=f"included file not found: {stmt.path}",
                        file=file_path,
                    ))
                    continue
                if canonical not in docs:
                    try:
                        docs[canonical] = parse_file(target, env=env_map)
                    except ParseError as exc:
                        report.add(Diagnostic(
                            code=DiagnosticCode.PARSE_ERROR,
                            severity=Severity.ERROR,
                            reason=str(exc),
                            file=getattr(exc, "source", canonical),
                            line=getattr(exc, "line", None),
                        ))
                        continue
                process_body(
                    docs[canonical].body,
                    file_dir=target.parent,
                    file_path=canonical,
                    active=True,
                )
            elif isinstance(stmt, IncludeOnce):
                if not active:
                    continue
                target = (file_dir / stmt.path).resolve()
                canonical = str(target)
                if canonical in seen_includes:
                    continue
                seen_includes.add(canonical)
                if not target.is_file():
                    report.add(Diagnostic(
                        code=DiagnosticCode.INCLUDE_MISSING,
                        severity=Severity.ERROR,
                        reason=f"included file not found: {stmt.path}",
                        file=file_path,
                    ))
                    continue
                if canonical not in docs:
                    try:
                        docs[canonical] = parse_file(target, env=env_map)
                    except ParseError as exc:
                        report.add(Diagnostic(
                            code=DiagnosticCode.PARSE_ERROR,
                            severity=Severity.ERROR,
                            reason=str(exc),
                            file=getattr(exc, "source", canonical),
                            line=getattr(exc, "line", None),
                        ))
                        continue
                process_body(
                    docs[canonical].body,
                    file_dir=target.parent,
                    file_path=canonical,
                    active=True,
                )
            elif isinstance(stmt, Ifdef):
                taken = env_is_set(env_map, stmt.var)
                inner_active = active and taken
                process_body(stmt.body, file_dir=file_dir, file_path=file_path, active=inner_active)
            elif isinstance(stmt, Ifndef):
                taken = env_is_set(env_map, stmt.var)
                inner_active = active and not taken
                process_body(stmt.body, file_dir=file_dir, file_path=file_path, active=inner_active)
            elif isinstance(stmt, Set):
                    if active:
                        flat[(stmt.section, stmt.key)] = stmt.value

    canonical_entry = str(entry.resolve())
    if canonical_entry in docs:
        process_body(
            docs[canonical_entry].body,
            file_dir=entry.parent,
            file_path=canonical_entry,
            active=True,
        )

    resolved: dict[tuple[str, str], str] = {}
    unresolved: dict[tuple[str, str], str] = {}

    def expand_value(
        value: Value,
        *,
        visiting: set[tuple[str, str]],
        pass_num: int,
    ) -> tuple[str, bool]:
        if pass_num > pass_limit:
            return "", False
        if isinstance(value, Literal):
            return value.text, True
        if isinstance(value, Env):
            v = env_map.get(value.var, "")
            return v, True
        if isinstance(value, EnvDefault):
            if env_is_set(env_map, value.var):
                return env_map[value.var], True
            return expand_value(value.default, visiting=visiting, pass_num=pass_num + 1)
        if isinstance(value, EnvAlternate):
            if env_is_set(env_map, value.var):
                return expand_value(value.alternate, visiting=visiting, pass_num=pass_num + 1)
            return "", True
        if isinstance(value, Concat):
            parts: list[str] = []
            ok = True
            for part in value.parts:
                s, part_ok = expand_value(part, visiting=visiting, pass_num=pass_num + 1)
                parts.append(s)
                if not part_ok:
                    ok = False
            return "".join(parts), ok
        if isinstance(value, Ref):
            ref_key = (value.section, value.key)
            if ref_key in visiting:
                report.add(Diagnostic(
                    code=DiagnosticCode.CIRCULAR_REF,
                    severity=Severity.ERROR,
                    reason=f"circular reference detected involving $( {value.section}.{value.key})",
                    section=value.section,
                    key=value.key,
                ))
                return f"<circular:{value.section}.{value.key}>", False
            if ref_key not in flat:
                    report.add(Diagnostic(
                        code=DiagnosticCode.UNRESOLVED_REF,
                        severity=Severity.WARNING,
                        reason=f"reference to undefined key $({value.section}.{value.key})",
                        section=value.section,
                        key=value.key,
                    ))
                    return f"<unresolved:{value.section}.{value.key}>", False
            new_visiting = set(visiting)
            new_visiting.add(ref_key)
            return expand_value(
                flat[ref_key],
                visiting=new_visiting,
                pass_num=pass_num + 1,
            )
        return "", False

    for sk, vnode in flat.items():
            section, key = sk
            result, ok = expand_value(vnode, visiting=set(), pass_num=0)
            if ok:
                resolved[sk] = result
            else:
                unresolved[sk] = result

    return EffectiveConfig(
        values=resolved,
        unresolved=unresolved,
        diagnostics=report,
    )
