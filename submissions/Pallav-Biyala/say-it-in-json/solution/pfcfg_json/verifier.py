"""Equivalence verification between legacy .pfcfg evaluation and pfcfg-json/v1 evaluation.

Runs the two evaluators independently and compares effective settings.
The verifier is capable of FAILING — it is not a vacuous wrapper around the
same underlying source.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticReport,
    Severity,
)
from .evaluator_json import evaluate_json_entry, evaluate_json_document
from .evaluator_legacy import evaluate_pfcfg_entry


@dataclass
class KeyMismatch:
    section: str
    key: str
    legacy_value: str | None
    json_value: str | None
    legacy_resolved: bool
    json_resolved: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "section": self.section,
            "key": self.key,
            "legacy_value": self.legacy_value,
            "json_value": self.json_value,
            "legacy_resolved": self.legacy_resolved,
            "json_resolved": self.json_resolved,
        }


@dataclass
class VerifyResult:
    entry: str
    env_name: str
    env: dict[str, str]
    passed: bool
    missing_in_json: list[tuple[str, str]]
    missing_in_legacy: list[tuple[str, str]]
    value_mismatches: list[KeyMismatch]
    unresolved_mismatches: list[KeyMismatch]
    legacy_diagnostics: DiagnosticReport
    json_diagnostics: DiagnosticReport
    report: DiagnosticReport

    def to_dict(self) -> dict[str, object]:
        return {
            "entry": self.entry,
            "env_name": self.env_name,
            "passed": self.passed,
            "missing_in_json": [
                {"section": s, "key": k} for s, k in self.missing_in_json
            ],
            "missing_in_legacy": [
                {"section": s, "key": k} for s, k in self.missing_in_legacy
            ],
            "value_mismatches": [m.to_dict() for m in self.value_mismatches],
            "unresolved_mismatches": [m.to_dict() for m in self.unresolved_mismatches],
            "legacy_diagnostics": self.legacy_diagnostics.to_list(),
            "json_diagnostics": self.json_diagnostics.to_list(),
            "verification_diagnostics": self.report.to_list(),
        }


def _all_keys(
    values: dict[tuple[str, str], str],
    unresolved: dict[tuple[str, str], str],
) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set(values.keys())
    result.update(unresolved.keys())
    return result


def verify_entry(
    entry_pfcfg: str | Path,
    entry_json: str | Path,
    *,
    env: Mapping[str, str],
    env_name: str = "default",
) -> VerifyResult:
    """Verify that the legacy and JSON evaluators produce identical effective settings.

    The two evaluators are invoked independently:
    - legacy: evaluates the .pfcfg file via evaluator_legacy (which parses .pfcfg)
    - json: evaluates the .json file via evaluator_json (which parses JSON only)

    Returns a result struct with detailed per-key comparison. This function
    can FAIL — callers should check result.passed.
    """
    entry_str = str(entry_pfcfg)
    report = DiagnosticReport()

    legacy = evaluate_pfcfg_entry(entry_pfcfg, env=env)
    json_cfg = evaluate_json_entry(entry_json, env=env)

    legacy_keys = _all_keys(legacy.values, legacy.unresolved)
    json_keys = _all_keys(json_cfg.values, json_cfg.unresolved)

    missing_in_json = sorted(legacy_keys - json_keys)
    missing_in_legacy = sorted(json_keys - legacy_keys)
    value_mismatches: list[KeyMismatch] = []
    unresolved_mismatches: list[KeyMismatch] = []

    for sk in sorted(legacy_keys & json_keys):
        s, k = sk
        lv, lr = legacy.get(s, k)
        jv, jr = json_cfg.get(s, k)
        if lr and jr:
            if lv != jv:
                value_mismatches.append(KeyMismatch(
                    section=s,
                    key=k,
                    legacy_value=lv,
                    json_value=jv,
                    legacy_resolved=True,
                    json_resolved=True,
                ))
        elif lr != jr:
            unresolved_mismatches.append(KeyMismatch(
                section=s,
                key=k,
                legacy_value=lv,
                json_value=jv,
                legacy_resolved=lr,
                json_resolved=jr,
            ))
        else:
            if lv != jv:
                unresolved_mismatches.append(KeyMismatch(
                    section=s,
                    key=k,
                    legacy_value=lv,
                    json_value=jv,
                    legacy_resolved=False,
                    json_resolved=False,
                ))

    for s, k in missing_in_json:
        report.add(Diagnostic(
            code=DiagnosticCode.VERIFY_MISSING_KEY,
            severity=Severity.ERROR,
            reason=f"key present in legacy evaluation but absent from JSON evaluation",
            section=s,
            key=k,
            file=entry_str,
        ))
    for s, k in missing_in_legacy:
        report.add(Diagnostic(
            code=DiagnosticCode.VERIFY_EXTRA_KEY,
            severity=Severity.ERROR,
            reason=f"key present in JSON evaluation but absent from legacy evaluation",
            section=s,
            key=k,
            file=entry_str,
        ))
    for mm in value_mismatches:
        report.add(Diagnostic(
            code=DiagnosticCode.VERIFY_MISMATCH,
            severity=Severity.ERROR,
            reason=f"resolved value mismatch: legacy={mm.legacy_value!r} json={mm.json_value!r}",
            section=mm.section,
            key=mm.key,
            file=entry_str,
            details={
                "legacy_value": mm.legacy_value,
                "json_value": mm.json_value,
            },
        ))
    for mm in unresolved_mismatches:
        report.add(Diagnostic(
            code=DiagnosticCode.VERIFY_MISMATCH,
            severity=Severity.WARNING,
            reason=f"unresolved/resolved state mismatch: legacy(resolved={mm.legacy_resolved})={mm.legacy_value!r} json(resolved={mm.json_resolved})={mm.json_value!r}",
            section=mm.section,
            key=mm.key,
            file=entry_str,
        ))

    passed = (
        not missing_in_json
        and not missing_in_legacy
        and not value_mismatches
        and not unresolved_mismatches
    )

    return VerifyResult(
        entry=entry_str,
        env_name=env_name,
        env=dict(env),
        passed=passed,
        missing_in_json=missing_in_json,
        missing_in_legacy=missing_in_legacy,
        value_mismatches=value_mismatches,
        unresolved_mismatches=unresolved_mismatches,
        legacy_diagnostics=legacy.diagnostics,
        json_diagnostics=json_cfg.diagnostics,
        report=report,
    )
