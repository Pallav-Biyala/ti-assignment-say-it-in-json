"""Independent evaluator for pfcfg-json/v1 JSON documents.

This module operates ONLY on JSON data — it never reads or parses .pfcfg files.
It reads JSON, resolves includes against other JSON files, and evaluates to
effective settings. The reference implementation in evaluator_legacy.py handles
.pfcfg; this one is deliberately separate so equivalence verification proves the
JSON migration preserves semantics.
"""

from __future__ import annotations

import json
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
    FORMAT,
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
    document_from_json,
    env_is_set,
)

DEFAULT_EXPANSION_PASS_LIMIT = 100


class JsonEvalError(Exception):
    pass


@dataclass(frozen=True)
class JsonEffectiveConfig:
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


def _load_json_document(path: Path) -> Document:
    text = path.read_text(encoding="utf-8")
    raw = json.loads(text)
    return document_from_json(raw)


def evaluate_json_entry(
    entry_json_path: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    pass_limit: int = DEFAULT_EXPANSION_PASS_LIMIT,
    json_root: Path | None = None,
) -> JsonEffectiveConfig:
    """Evaluate a pfcfg-json/v1 entry JSON file.

    This function never reads .pfcfg files. It loads JSON, follows
    include/include_once directives pointing at .json siblings, and
    computes effective settings independently.
    """
    env_map: Mapping[str, str] = os.environ if env is None else env
    report = DiagnosticReport()
    entry = Path(entry_json_path)
    if not entry.is_file():
        report.add(Diagnostic(
            code=DiagnosticCode.INCLUDE_MISSING,
            severity=Severity.ERROR,
            reason=f"entry JSON file not found: {entry}",
            file=str(entry),
        ))
        return JsonEffectiveConfig(values={}, unresolved={}, diagnostics=report)

    root = json_root if json_root is not None else entry.parent

    flat: dict[tuple[str, str], Value] = {}
    seen_includes: set[str] = set()
    loaded_docs: dict[str, Document] = {}

    def load_doc(target: Path) -> Document | None:
        canonical = str(target.resolve())
        if canonical in loaded_docs:
            return loaded_docs[canonical]
        if not target.is_file():
            report.add(Diagnostic(
                code=DiagnosticCode.INCLUDE_MISSING,
                severity=Severity.ERROR,
                reason=f"included JSON not found: {target}",
                file=str(target),
            ))
            return None
        try:
            doc = _load_json_document(target)
        except (ValueError, KeyError, TypeError) as exc:
            report.add(Diagnostic(
                code=DiagnosticCode.PARSE_ERROR,
                severity=Severity.ERROR,
                reason=f"invalid JSON document: {exc}",
                file=str(target),
            ))
            return None
        loaded_docs[canonical] = doc
        return doc

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
                target_raw = stmt.path
                if not target_raw.endswith(".json"):
                    target_raw = target_raw.replace(".pfcfg", ".json")
                    if not target_raw.endswith(".json"):
                        target_raw = target_raw + ".json"
                target = (file_dir / target_raw).resolve()
                doc = load_doc(target)
                if doc is None:
                    continue
                process_body(
                    doc.body,
                    file_dir=target.parent,
                    file_path=str(target),
                    active=True,
                )
            elif isinstance(stmt, IncludeOnce):
                if not active:
                    continue
                target_raw = stmt.path
                if not target_raw.endswith(".json"):
                    target_raw = target_raw.replace(".pfcfg", ".json")
                    if not target_raw.endswith(".json"):
                        target_raw = target_raw + ".json"
                target = (file_dir / target_raw).resolve()
                canonical = str(target)
                if canonical in seen_includes:
                    continue
                seen_includes.add(canonical)
                doc = load_doc(target)
                if doc is None:
                    continue
                process_body(
                    doc.body,
                    file_dir=target.parent,
                    file_path=str(target),
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

    entry_canonical = str(entry.resolve())
    entry_doc = load_doc(entry)
    if entry_doc is None:
        return JsonEffectiveConfig(values={}, unresolved={}, diagnostics=report)
    loaded_docs[entry_canonical] = entry_doc
    process_body(
        entry_doc.body,
        file_dir=entry.parent,
        file_path=entry_canonical,
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
                    reason=f"circular reference detected involving $({value.section}.{value.key})",
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

    return JsonEffectiveConfig(
        values=resolved,
        unresolved=unresolved,
        diagnostics=report,
    )


def evaluate_json_document(
    doc: Document,
    *,
    env: Mapping[str, str] | None = None,
    pass_limit: int = DEFAULT_EXPANSION_PASS_LIMIT,
    doc_dir: Path | None = None,
) -> JsonEffectiveConfig:
    """Evaluate an already-loaded JSON Document AST.

    Includes are still resolved against the filesystem when present.
    This never parses .pfcfg.
    """
    env_map: Mapping[str, str] = os.environ if env is None else env
    report = DiagnosticReport()

    flat: dict[tuple[str, str], Value] = {}
    seen_includes: set[str] = set()
    loaded_docs: dict[str, Document] = {}
    loaded_docs["<memory>"] = doc

    base_dir = doc_dir if doc_dir is not None else Path.cwd()

    def load_doc(target: Path) -> Document | None:
        canonical = str(target.resolve())
        if canonical in loaded_docs:
            return loaded_docs[canonical]
        if not target.is_file():
            report.add(Diagnostic(
                code=DiagnosticCode.INCLUDE_MISSING,
                severity=Severity.ERROR,
                reason=f"included JSON not found: {target}",
                file=str(target),
            ))
            return None
        try:
            d = _load_json_document(target)
        except (ValueError, KeyError, TypeError) as exc:
            report.add(Diagnostic(
                code=DiagnosticCode.PARSE_ERROR,
                severity=Severity.ERROR,
                reason=f"invalid JSON document: {exc}",
                file=str(target),
            ))
            return None
        loaded_docs[canonical] = d
        return d

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
                target_raw = stmt.path
                if not target_raw.endswith(".json"):
                    target_raw = target_raw.replace(".pfcfg", ".json")
                    if not target_raw.endswith(".json"):
                        target_raw = target_raw + ".json"
                target = (file_dir / target_raw).resolve()
                d = load_doc(target)
                if d is None:
                    continue
                process_body(d.body, file_dir=target.parent, file_path=str(target), active=True)
            elif isinstance(stmt, IncludeOnce):
                if not active:
                    continue
                target_raw = stmt.path
                if not target_raw.endswith(".json"):
                    target_raw = target_raw.replace(".pfcfg", ".json")
                    if not target_raw.endswith(".json"):
                        target_raw = target_raw + ".json"
                target = (file_dir / target_raw).resolve()
                canonical = str(target)
                if canonical in seen_includes:
                    continue
                seen_includes.add(canonical)
                d = load_doc(target)
                if d is None:
                    continue
                process_body(d.body, file_dir=target.parent, file_path=str(target), active=True)
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

    process_body(doc.body, file_dir=base_dir, file_path="<memory>", active=True)

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
            return env_map.get(value.var, ""), True
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
                    reason=f"circular reference detected involving $({value.section}.{value.key})",
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
            return expand_value(flat[ref_key], visiting=new_visiting, pass_num=pass_num + 1)
        return "", False

    for sk, vnode in flat.items():
        section, key = sk
        result, ok = expand_value(vnode, visiting=set(), pass_num=0)
        if ok:
            resolved[sk] = result
        else:
            unresolved[sk] = result

    return JsonEffectiveConfig(
        values=resolved,
        unresolved=unresolved,
        diagnostics=report,
    )
