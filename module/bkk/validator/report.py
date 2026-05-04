"""Validation findings + renderers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    path: str
    message: str


@dataclass
class Report:
    bundle: str
    findings: list[Finding] = field(default_factory=list)
    # Per-(rule_id, path) cap: collapse runaway repetitions of the same
    # finding (e.g. thousands of offset-out-of-bounds entries on a broken
    # juan) into one entry plus a tally. Keeps the report scannable.
    max_per_rule_path: int = 5
    _tallies: dict[tuple[str, str], int] = field(default_factory=dict)

    def add(self, rule_id: str, severity: Severity, path: str, message: str) -> None:
        key = (rule_id, path)
        n = self._tallies.get(key, 0)
        self._tallies[key] = n + 1
        if n < self.max_per_rule_path:
            self.findings.append(Finding(rule_id, severity, path, message))
        elif n == self.max_per_rule_path:
            self.findings.append(Finding(
                rule_id, severity, path,
                f"... further {rule_id} findings on this file suppressed",
            ))

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    def counts(self) -> dict[str, int]:
        out = {"error": 0, "warning": 0}
        for f in self.findings:
            out[f.severity] += 1
        return out

    def render_text(self) -> str:
        lines = [self.bundle]
        if not self.findings:
            lines.append("OK")
        else:
            for f in self.findings:
                lines.append(
                    f"[{f.severity:<7}] {f.rule_id}  {f.path}: {f.message}"
                )
        c = self.counts()
        lines.append(f"{c['error']} error(s), {c['warning']} warning(s)")
        return "\n".join(lines)

    def render_json(self) -> str:
        return json.dumps(
            {
                "bundle": self.bundle,
                "findings": [
                    {
                        "rule_id": f.rule_id,
                        "severity": f.severity,
                        "path": f.path,
                        "message": f.message,
                    }
                    for f in self.findings
                ],
                "summary": self.counts(),
            },
            indent=2,
            ensure_ascii=False,
        )
