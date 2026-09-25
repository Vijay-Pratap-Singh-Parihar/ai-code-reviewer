"""Factory fixtures shared by `verify`'s tests."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from revu.models import EvidenceItem, Finding, FindingCategory, Severity

FindingFactory = Callable[..., Finding]
EvidenceFactory = Callable[..., EvidenceItem]


@pytest.fixture
def finding_factory() -> FindingFactory:
    def _make(
        *,
        file_path: str = "src/app.py",
        line_start: int = 10,
        line_end: int = 12,
        category: FindingCategory = FindingCategory.CORRECTNESS,
        severity: Severity = Severity.MEDIUM,
        message: str = "something looks off",
        evidence: list[EvidenceItem] | None = None,
        confidence: float = 0.7,
        agent_name: str = "diff_only",
    ) -> Finding:
        return Finding(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            category=category,
            severity=severity,
            message=message,
            evidence=evidence or [],
            confidence=confidence,
            agent_name=agent_name,
        )

    return _make


@pytest.fixture
def evidence_factory() -> EvidenceFactory:
    def _make(
        *,
        file_path: str = "src/app.py",
        line_start: int = 1,
        line_end: int = 2,
        reason: str = "relevant context",
    ) -> EvidenceItem:
        return EvidenceItem(
            file_path=file_path, line_start=line_start, line_end=line_end, reason=reason
        )

    return _make
