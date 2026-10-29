from revu.github_review import (
    ReviewEvidence,
    ReviewFinding,
    build_review,
    commentable_ranges,
    summary_only,
)

DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,4 +10,5 @@ def total(values):
     result = 0
-    for i in range(n):
+    for i in range(n + 1):
+        log(i)
         result += values[i]
     return result
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x = 1
-y = 2
"""


def finding(path: str, start: int, end: int | None = None, severity: str = "high") -> ReviewFinding:
    return ReviewFinding(
        path=path,
        line_start=start,
        line_end=end or start,
        severity=severity,
        category="correctness",
        message=f"issue at {path}:{start}",
        confidence=0.8,
        evidence=(
            ReviewEvidence(path=path, line_start=start, line_end=start, reason="loop bound"),
        ),
    )


def test_only_new_file_lines_inside_hunks_are_commentable() -> None:
    assert commentable_ranges(DIFF) == {"app.py": [(10, 14)]}


def test_findings_on_changed_lines_go_inline_and_the_rest_into_the_summary() -> None:
    payload = build_review(
        [
            finding("app.py", 11),
            finding("app.py", 40),
            finding("other.py", 3),
            finding("old.py", 1),
        ],
        DIFF,
        model_label="qwen3.5:9b (Local Ollama)",
    )

    assert payload.inline == 1 and payload.in_summary == 3
    [comment] = payload.comments
    assert comment["path"] == "app.py" and comment["line"] == 11 and comment["side"] == "RIGHT"
    assert "start_line" not in comment
    assert "`app.py:40`" in payload.body and "`other.py:3`" in payload.body
    assert "qwen3.5:9b (Local Ollama)" in payload.body
    assert "never approves or blocks" in payload.body


def test_a_range_inside_one_hunk_becomes_a_multi_line_comment() -> None:
    [comment] = build_review([finding("app.py", 11, 13)], DIFF).comments
    assert (comment["start_line"], comment["line"]) == (11, 13)
    assert comment["start_side"] == "RIGHT"


def test_a_range_that_starts_before_the_hunk_is_anchored_on_its_last_line() -> None:
    [comment] = build_review([finding("app.py", 5, 12)], DIFF).comments
    assert comment["line"] == 12 and "start_line" not in comment


def test_the_request_is_a_comment_never_an_approval() -> None:
    request = build_review([finding("app.py", 11)], DIFF).to_request("abc123")
    assert request["event"] == "COMMENT" and request["commit_id"] == "abc123"


def test_most_severe_first_and_inline_comments_are_capped() -> None:
    findings = [finding("app.py", 10 + i % 5, severity="low") for i in range(4)] + [
        finding("app.py", 12, severity="critical")
    ]
    payload = build_review(findings, DIFF, max_inline=2)
    assert payload.inline == 2 and payload.in_summary == 3
    assert payload.comments[0]["body"].startswith("**Critical")


def test_comment_body_carries_evidence_and_confidence() -> None:
    [comment] = build_review([finding("app.py", 11)], DIFF).comments
    assert "<details><summary>Evidence</summary>" in comment["body"]
    assert "`app.py:11`: loop bound" in comment["body"]
    assert "confidence 0.80" in comment["body"]


def test_no_findings_still_says_the_change_was_reviewed() -> None:
    payload = build_review([], DIFF)
    assert payload.comments == [] and "no issues" in payload.body


def test_summary_only_keeps_every_finding_in_the_body() -> None:
    payload = summary_only([finding("app.py", 11), finding("app.py", 12)])
    assert payload.comments == [] and payload.in_summary == 2
