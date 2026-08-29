"""In-memory representation of pfcfg-json/v1.

Preserves statement order. Does not parse .pfcfg, evaluate, or convert.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import IO, Union

FORMAT = "pfcfg-json/v1"


class ModelError(ValueError):
    """Raised when a document does not match pfcfg-json/v1."""


# --- values -----------------------------------------------------------------


@dataclass(frozen=True)
class Literal:
    """A configuration string. Never a JSON bool or number."""

    text: str


@dataclass(frozen=True)
class Env:
    """${VAR} with no default or alternate."""

    var: str


@dataclass(frozen=True)
class EnvDefault:
    """${VAR:-default}."""

    var: str
    default: Value


@dataclass(frozen=True)
class EnvAlternate:
    """${VAR:+alternate}."""

    var: str
    alternate: Value


@dataclass(frozen=True)
class Ref:
    """$(section.key). Section names may contain dots."""

    section: str
    key: str


@dataclass(frozen=True)
class Concat:
    """Ordered concatenation of nested values."""

    parts: tuple[Value, ...]


Value = Union[Literal, Env, EnvDefault, EnvAlternate, Ref, Concat]


# --- statements -------------------------------------------------------------


@dataclass(frozen=True)
class Include:
    path: str


@dataclass(frozen=True)
class IncludeOnce:
    path: str


@dataclass(frozen=True)
class Ifdef:
    var: str
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class Ifndef:
    var: str
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class Set:
    """Last-wins assignment. section may contain dots."""

    section: str
    key: str
    value: Value


Assignment = Set

Stmt = Union[Include, IncludeOnce, Ifdef, Ifndef, Set]


# --- document ---------------------------------------------------------------


@dataclass(frozen=True)
class Document:
    source: str
    body: tuple[Stmt, ...]
    format: str = FORMAT

    def __post_init__(self) -> None:
        if self.format != FORMAT:
            raise ModelError(f"unsupported format: {self.format!r}")


# --- JSON: values -----------------------------------------------------------


def value_to_json(value: Value) -> dict:
    if isinstance(value, Literal):
        if not isinstance(value.text, str):
            raise ModelError("literal values must be strings")
        return {"lit": value.text}
    if isinstance(value, Env):
        return {"env": value.var}
    if isinstance(value, EnvDefault):
        return {"env": value.var, "default": value_to_json(value.default)}
    if isinstance(value, EnvAlternate):
        return {"env": value.var, "alternate": value_to_json(value.alternate)}
    if isinstance(value, Ref):
        return {"ref": {"section": value.section, "key": value.key}}
    if isinstance(value, Concat):
        return {"concat": [value_to_json(part) for part in value.parts]}
    raise ModelError(f"unrecognized value type: {type(value)!r}")


def value_from_json(data: object) -> Value:
    obj = _expect_dict(data, "value")
    keys = set(obj)
    if keys == {"lit"}:
        text = obj["lit"]
        if not isinstance(text, str):
            raise ModelError("literal values must be strings")
        return Literal(text)
    if keys == {"concat"}:
        parts = obj["concat"]
        if not isinstance(parts, list):
            raise ModelError("concat must be a list")
        return Concat(tuple(value_from_json(part) for part in parts))
    if keys == {"ref"}:
        ref = _expect_dict(obj["ref"], "ref")
        if set(ref) != {"section", "key"}:
            raise ModelError("ref must have exactly section and key")
        section, key = ref["section"], ref["key"]
        if not isinstance(section, str) or not isinstance(key, str):
            raise ModelError("ref section and key must be strings")
        return Ref(section, key)
    if "env" in obj:
        var = obj["env"]
        if not isinstance(var, str):
            raise ModelError("env var must be a string")
        if keys == {"env"}:
            return Env(var)
        if keys == {"env", "default"}:
            return EnvDefault(var, value_from_json(obj["default"]))
        if keys == {"env", "alternate"}:
            return EnvAlternate(var, value_from_json(obj["alternate"]))
        raise ModelError("env cannot combine default and alternate")
    raise ModelError(f"unrecognized value object: {sorted(keys)}")


# --- JSON: statements -------------------------------------------------------


def stmt_to_json(stmt: Stmt) -> dict:
    if isinstance(stmt, Include):
        return {"op": "include", "path": stmt.path}
    if isinstance(stmt, IncludeOnce):
        return {"op": "include_once", "path": stmt.path}
    if isinstance(stmt, Ifdef):
        return {
            "op": "ifdef",
            "var": stmt.var,
            "body": [stmt_to_json(s) for s in stmt.body],
        }
    if isinstance(stmt, Ifndef):
        return {
            "op": "ifndef",
            "var": stmt.var,
            "body": [stmt_to_json(s) for s in stmt.body],
        }
    if isinstance(stmt, Set):
        return {
            "op": "set",
            "section": stmt.section,
            "key": stmt.key,
            "value": value_to_json(stmt.value),
        }
    raise ModelError(f"unrecognized statement type: {type(stmt)!r}")


def stmt_from_json(data: object) -> Stmt:
    obj = _expect_dict(data, "statement")
    op = obj.get("op")
    if op == "include":
        _expect_keys(obj, {"op", "path"}, "include")
        return Include(_expect_str(obj["path"], "include path"))
    if op == "include_once":
        _expect_keys(obj, {"op", "path"}, "include_once")
        return IncludeOnce(_expect_str(obj["path"], "include_once path"))
    if op == "ifdef":
        _expect_keys(obj, {"op", "var", "body"}, "ifdef")
        return Ifdef(
            _expect_str(obj["var"], "ifdef var"),
            _body_from_json(obj["body"]),
        )
    if op == "ifndef":
        _expect_keys(obj, {"op", "var", "body"}, "ifndef")
        return Ifndef(
            _expect_str(obj["var"], "ifndef var"),
            _body_from_json(obj["body"]),
        )
    if op == "set":
        _expect_keys(obj, {"op", "section", "key", "value"}, "set")
        return Set(
            _expect_str(obj["section"], "set section"),
            _expect_str(obj["key"], "set key"),
            value_from_json(obj["value"]),
        )
    raise ModelError(f"unrecognized op: {op!r}")


def _body_from_json(data: object) -> tuple[Stmt, ...]:
    if not isinstance(data, list):
        raise ModelError("body must be a list of statements")
    return tuple(stmt_from_json(item) for item in data)


# --- JSON: document ---------------------------------------------------------


def document_to_json(document: Document) -> dict:
    if document.format != FORMAT:
        raise ModelError(f"unsupported format: {document.format!r}")
    return {
        "format": FORMAT,
        "source": document.source,
        "body": [stmt_to_json(stmt) for stmt in document.body],
    }


def document_from_json(data: object) -> Document:
    obj = _expect_dict(data, "document")
    _expect_keys(obj, {"format", "source", "body"}, "document")
    fmt = obj["format"]
    if fmt != FORMAT:
        raise ModelError(f"unsupported format: {fmt!r}")
    return Document(
        source=_expect_str(obj["source"], "source"),
        body=_body_from_json(obj["body"]),
        format=FORMAT,
    )


def dumps(document: Document, *, indent: int | None = 2) -> str:
    return json.dumps(document_to_json(document), indent=indent) + (
        "\n" if indent is not None else ""
    )


def loads(text: str) -> Document:
    return document_from_json(json.loads(text))


def dump(document: Document, fp: IO[str], *, indent: int | None = 2) -> None:
    fp.write(dumps(document, indent=indent))


def load(fp: IO[str]) -> Document:
    return document_from_json(json.load(fp))


# --- helpers ----------------------------------------------------------------


def _expect_dict(data: object, label: str) -> dict:
    if not isinstance(data, dict):
        raise ModelError(f"{label} must be an object")
    return data


def _expect_str(data: object, label: str) -> str:
    if not isinstance(data, str):
        raise ModelError(f"{label} must be a string")
    return data


def _expect_keys(obj: dict, expected: set[str], label: str) -> None:
    keys = set(obj)
    if keys != expected:
        raise ModelError(f"{label} must have keys {sorted(expected)}, got {sorted(keys)}")
