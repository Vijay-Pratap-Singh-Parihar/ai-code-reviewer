"""What the run page says about how a review went, from the worker's notes."""

from api.services.analysis import run_notices


def test_a_clean_review_has_nothing_to_say() -> None:
    assert run_notices({"agent": "diff_only", "source": "github"}) == []


def test_skipped_files_parts_and_a_partial_failure_are_explained() -> None:
    notices = run_notices(
        {
            "skipped_files": ["yarn.lock", "a.min.js", "vendor/x.go", "api/types.pb.go"],
            "parts": 3,
            "stopped_reason": "parts_failed (1 of 3)",
        }
    )

    assert notices == [
        "Left out 4 lockfile or generated files: yarn.lock, a.min.js, vendor/x.go and 1 more.",
        "Large diff: reviewed in 3 parts.",
        "1 of 3 parts got no answer from the model; files in those parts have no findings.",
    ]


def test_each_stop_reason_reads_as_a_sentence() -> None:
    assert run_notices({"stopped_reason": "output_token_limit"}) == [
        "The model's answer hit its length limit; some findings may be missing."
    ]
    assert run_notices({"stopped_reason": "max_tool_rounds_reached (8)"})[0].startswith(
        "The cross-file agent ran out"
    )
    assert run_notices({"agent_fallback": "diff_only: no tools", "skipped_files": ["go.sum"]}) == [
        "Reviewed from the diff only: the configured model can't call tools.",
        "Left out 1 lockfile or generated file: go.sum.",
    ]
