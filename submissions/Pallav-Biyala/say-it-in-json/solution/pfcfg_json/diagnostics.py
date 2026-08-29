"""Diagnostics and reporting for pfcfg migration and evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class DiagnosticCode(str, Enum):
    INCLUDE_CYCLE = "INCLUDE_CYCLE"
    INCLUDE_MISSING = "INCLUDE_MISSING"
    PARSE_ERROR = "PARSE_ERROR"
    CIRCULAR_REF = "CIRCULAR_REF"
    EXPANSION_LIMIT = "EXPANSION_LIMIT"
    UNRESOLVED_ENV = "UNRESOLVED_ENV"
    UNRESOLVED_REF = "UNRESOLVED_REF"
    UNMIGRATABLE_ENV_NO_DEFAULT = "UNMIGRATABLE_ENV_NO_DEFAULT"
    UNMIGRATABLE_DYNAMIC_REF = "UNMIGRATABLE_DYNAMIC_REF"
    RISKY_CONDITIONAL_INCLUDE = "RISKY_CONDITIONAL_INCLUDE"
    RISKY_LAST_WINS_OVERLAP = "RISKY_LAST_WINS_OVERLAP"
    VERIFY_MISMATCH = "VERIFY_MISMATCH"
    VERIFY_MISSING_KEY = "VERIFY_MISSING_KEY"
    VERIFY_EXTRA_KEY = "VERIFY_EXTRA_KEY"


@dataclass
class Diagnostic:
    code: DiagnosticCode
    severity: Severity
    reason: str
    file: str | None = None
    section: str | None = None
    key: str | None = None
    line: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "code": self.code.value,
            "severity": self.severity.value,
            "reason": self.reason,
        }
        if self.file is not None:
            result["file"] = self.file
        if self.section is not None:
            result["section"] = self.section
        if self.key is not None:
            result["key"] = self.key
        if self.line is not None:
            result["line"] = self.line
        if self.details:
            result["details"] = self.details
        return result

    @property
    def is_blocking(self) -> bool:
        return self.severity == Severity.ERROR

    @property
    def is_unmigratable(self) -> bool:
        return self.code in (
            DiagnosticCode.UNMIGRATABLE_ENV_NO_DEFAULT,
            DiagnosticCode.UNMIGRATABLE_DYNAMIC_REF,
            DiagnosticCode.CIRCULAR_REF,
        )


class DiagnosticReport:
    def __init__(self) -> None:
        self._items: list[Diagnostic] = []

    def add(self, diag: Diagnostic) -> None:
        self._items.append(diag)

    def extend(self, diags: list[Diagnostic]) -> None:
        self._items.extend(diags)

    @property
    def items(self) -> list[Diagnostic]:
        return list(self._items)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self._items if d.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self._items if d.severity == Severity.WARNING]

    @property
    def unmigratable(self) -> list[Diagnostic]:
        return [d for d in self._items if d.is_unmigratable]

    @property
    def has_errors(self) -> bool:
        return any(d.severity == Severity.ERROR for d in self._items)

    def to_list(self) -> list[dict[str, Any]]:
        return [d.to_dict() for d in self._items]

    def to_ndjson(self) -> str:
        import json

        return "\n".join(json.dumps(d.to_dict()) for d in self._items) + (
            "\n" if self._items else ""
        )

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
        for d in self._items:
            counts[d.severity.value] = counts.get(d.severity.value, 0) + 1
        counts["total"] = len(self._items)
        counts["unmigratable"] = len(self.unmigratable)
        return counts
