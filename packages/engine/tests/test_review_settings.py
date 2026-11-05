"""`revu.review_settings`: ignore globs, the token estimate, output limits."""

import pytest
from pydantic import ValidationError
from revu.models import Finding, FindingCategory, Severity
from revu.review_settings import (
    ANSWER_ALLOWANCE,
    PROMPT_OVERHEAD,
    ReviewSettings,
    apply_output_limits,
    drop_ignored,
    estimate_review_tokens,
)


def _block(path: str, line: str = "+x = 1") -> str:
    header = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
    return f"{header}@@ -1 +1 @@\n-x = 0\n{line}\n"


DIFF = _block("src/app.py") + _block("docs/guide.md") + _block("tests/__snapshots__/a.snap")


def test_ignore_globs_drop_matching_files_only() -> None:
    result = drop_ignored(DIFF, ["docs/**", "*.snap"])

    assert result.ignored == ["docs/guide.md", "tests/__snapshots__/a.snap"]
    assert "src/app.py" in result.diff and "docs/guide.md" not in result.diff


def test_no_globs_leaves_the_diff_alone() -> None:
    assert drop_ignored(DIFF, []).diff == DIFF


def test_the_estimate_counts_what_the_model_will_see() -> None:
    small = estimate_review_tokens(_block("src/app.py"))
    assert PROMPT_OVERHEAD + ANSWER_ALLOWANCE < small < PROMPT_OVERHEAD + ANSWER_ALLOWANCE + 100
    # A lockfile is dropped before review, so it costs nothing.
    assert estimate_review_tokens(_block("package-lock.json")) == 0
    big = "".join(_block(f"src/m{i}.py", "+" + "value = compute(value) " * 40) for i in range(200))
    assert estimate_review_tokens(big) > 10 * small  # several parts, each with its allowance


def _finding(severity: Severity, confidence: float, line: int = 1) -> Finding:
    return Finding(
        file_path="a.py",
        line_start=line,
        line_end=line,
        category=FindingCategory.CORRECTNESS,
        severity=severity,
        message="m",
        confidence=confidence,
        agent_name="diff_only",
    )


def test_output_limits_apply_the_threshold_then_the_cap() -> None:
    findings = [
        _finding(Severity.LOW, 0.9, 1),
        _finding(Severity.HIGH, 0.5, 2),
        _finding(Severity.CRITICAL, 0.4, 3),
        _finding(Severity.HIGH, 0.8, 4),
    ]

    result = apply_output_limits(
        findings, ReviewSettings(min_severity=Severity.MEDIUM, max_comments=2)
    )

    assert [f.line_start for f in result.findings] == [3, 4]  # critical, then the surer high
    assert (result.below_threshold, result.over_cap) == (1, 1)
    assert apply_output_limits(findings, ReviewSettings()).findings == findings


def test_settings_validate_and_fall_back_safely() -> None:
    assert ReviewSettings(ignore_globs=[" docs/** ", "", "docs/**"]).ignore_globs == ["docs/**"]
    with pytest.raises(ValidationError):
        ReviewSettings(max_comments=0)
    with pytest.raises(ValidationError):
        ReviewSettings(max_tokens_per_review=10)
    assert ReviewSettings.from_config({"review": {"max_comments": "lots"}}) == ReviewSettings()
    assert ReviewSettings.from_config(None) == ReviewSettings()
    assert ReviewSettings.from_config({"review": {"max_comments": 5}}).max_comments == 5
