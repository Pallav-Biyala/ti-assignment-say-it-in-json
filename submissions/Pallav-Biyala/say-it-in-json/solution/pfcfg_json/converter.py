"""Converter from .pfcfg to pfcfg-json/v1 JSON format.

Walks a directory of .pfcfg files, parses each into the AST, and emits the
corresponding .json file in an output tree. Also reports unmigratable and
risky items via diagnostics.
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
    Concat,
    Document,
    Env,
    EnvAlternate,
    EnvDefault,
    FORMAT,
    Ifdef,
    Ifndef,
    Include,
    IncludeOnce,
    Literal,
    Ref,
    Set,
    Stmt,
    Value,
    dumps,
)
from .parser import ParseError, parse_entry, parse_file


def _collect_value_diagnostics(
    value: Value,
    *,
    section: str,
    key: str,
    file: str,
    report: DiagnosticReport,
) -> None:
    if isinstance(value, Env):
        report.add(Diagnostic(
            code=DiagnosticCode.UNMIGRATABLE_ENV_NO_DEFAULT,
            severity=Severity.WARNING,
            reason=f"env var ${value.var} has no default; effective value depends on environment at evaluation time",
            file=file,
            section=section,
            key=key,
            details={"var": value.var},
        ))
    elif isinstance(value, EnvDefault):
        _collect_value_diagnostics(
            value.default,
            section=section,
            key=key,
            file=file,
            report=report,
        )
    elif isinstance(value, EnvAlternate):
        _collect_value_diagnostics(
            value.alternate,
            section=section,
            key=key,
            file=file,
            report=report,
        )
    elif isinstance(value, Concat):
        for part in value.parts:
            _collect_value_diagnostics(
                part,
                section=section,
                key=key,
                file=file,
                report=report,
            )
    elif isinstance(value, Ref):
        pass
    elif isinstance(value, Literal):
        pass


def _collect_stmt_diagnostics(
    stmt: Stmt,
    *,
    file: str,
    report: DiagnosticReport,
    outer_conditional: bool = False,
) -> None:
    if isinstance(stmt, Include):
        if outer_conditional:
            report.add(Diagnostic(
                code=DiagnosticCode.RISKY_CONDITIONAL_INCLUDE,
                severity=Severity.WARNING,
                reason=f"conditional @include {stmt.path}; include graph changes with environment",
                file=file,
                details={"path": stmt.path},
            ))
    elif isinstance(stmt, IncludeOnce):
        if outer_conditional:
            report.add(Diagnostic(
                code=DiagnosticCode.RISKY_CONDITIONAL_INCLUDE,
                severity=Severity.WARNING,
                reason=f"conditional @include_once {stmt.path}; include graph changes with environment",
                file=file,
                details={"path": stmt.path},
            ))
    elif isinstance(stmt, Ifdef) or isinstance(stmt, Ifndef):
        for inner in stmt.body:
            _collect_stmt_diagnostics(
                inner,
                file=file,
                report=report,
                outer_conditional=True,
            )
    elif isinstance(stmt, Set):
        _collect_value_diagnostics(
            stmt.value,
            section=stmt.section,
            key=stmt.key,
            file=file,
            report=report,
        )


@dataclass
class ConversionResult:
    files_converted: list[tuple[str, str]]
    report: DiagnosticReport

    @property
    def success(self) -> bool:
        return not self.report.has_errors


def _json_path_for(pfcfg_path: Path, src_root: Path, dst_root: Path) -> Path:
    rel = pfcfg_path.relative_to(src_root)
    return (dst_root / rel).with_suffix(".json")


def convert_tree(
    source_root: str | Path,
    output_root: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    entry_points: list[str] | None = None,
) -> ConversionResult:
    """Convert every .pfcfg file under source_root to a sibling .json tree.

    If entry_points are provided, additionally walks their active include
    closure so conditionally-included files are still converted (under all
    environments, the union of includes is converted).
    """
    env_map: Mapping[str, str] = os.environ if env is None else env
    src_root = Path(source_root)
    dst_root = Path(output_root)
    report = DiagnosticReport()
    converted: list[tuple[str, str]] = []

    all_pfcfg: set[Path] = set()
    for pfcfg in src_root.rglob("*.pfcfg"):
        if pfcfg.is_file():
            all_pfcfg.add(pfcfg.resolve())

    if entry_points:
        for ep in entry_points:
            ep_path = (src_root / ep).resolve()
            try:
                closure = parse_entry(ep_path, env=env_map)
                for canonical in closure:
                    all_pfcfg.add(Path(canonical))
            except ParseError as exc:
                report.add(Diagnostic(
                    code=DiagnosticCode.PARSE_ERROR,
                    severity=Severity.ERROR,
                    reason=str(exc),
                    file=getattr(exc, "source", str(ep_path)),
                    line=getattr(exc, "line", None),
                ))

    for pfcfg in sorted(all_pfcfg):
        rel_str = str(pfcfg)
        try:
            doc = parse_file(pfcfg, env=env_map)
        except ParseError as exc:
            report.add(Diagnostic(
                code=DiagnosticCode.PARSE_ERROR,
                severity=Severity.ERROR,
                reason=str(exc),
                file=getattr(exc, "source", rel_str),
                line=getattr(exc, "line", None),
            ))
            continue

        for stmt in doc.body:
            _collect_stmt_diagnostics(stmt, file=rel_str, report=report)

        dst = _json_path_for(pfcfg, src_root, dst_root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(dumps(doc), encoding="utf-8")
        converted.append((rel_str, str(dst)))

    return ConversionResult(files_converted=converted, report=report)


def convert_single(
    pfcfg_path: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    source_name: str | None = None,
) -> tuple[Document, DiagnosticReport]:
    env_map: Mapping[str, str] = os.environ if env is None else env
    report = DiagnosticReport()
    path = Path(pfcfg_path)
    src = source_name if source_name is not None else path.as_posix()
    try:
        doc = parse_file(path, env=env_map)
    except ParseError as exc:
        report.add(Diagnostic(
            code=DiagnosticCode.PARSE_ERROR,
            severity=Severity.ERROR,
            reason=str(exc),
            file=getattr(exc, "source", src),
            line=getattr(exc, "line", None),
        ))
        raise
    for stmt in doc.body:
        _collect_stmt_diagnostics(stmt, file=src, report=report)
    return doc, report
