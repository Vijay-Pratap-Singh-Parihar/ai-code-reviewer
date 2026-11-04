"""AACR-Bench records -> instances, with the GitHub calls replaced."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from revu_bench.adapters import aacr

RECORD: dict[str, Any] = {
    "change_line_count": 862,
    "project_main_language": "C++",
    "source_commit": "0c65673a6fd2421be8fbe613116077120adea068",
    "target_commit": "a050e422e23ce3eaee960b75ceff236b34f369b9",
    "githubPrUrl": "https://github.com/FreeCAD/FreeCAD/pull/19411",
    "comments": [
        {
            "is_ai_comment": True,
            "note": "getDirsFromFront(t) is called for every view; hoist it.",
            "path": "src/Mod/TechDraw/App/DrawProjGroup.cpp",
            "side": "right",
            "from_line": 1126,
            "to_line": 1123,
            "category": "Performance",
            "context": "File Level",
        },
        {"note": "   ", "path": "a.cpp", "from_line": 1, "to_line": 1},
        {"note": "missing path", "path": "", "from_line": 1, "to_line": 1},
        {
            "note": "Unchecked index into the views vector.",
            "path": "src/Mod/TechDraw/App/DrawProjGroup.cpp",
            "from_line": None,
            "to_line": 88,
            "category": "Code Defect",
            "context": "Repo Level",
        },
    ],
}


def fake_fetch(ref: aacr.PullRequestRef) -> aacr.PullRequestData:
    return aacr.PullRequestData(diff=f"diff for {ref.repo}#{ref.number}", title="T", body="B")


def test_a_record_becomes_an_instance_with_its_diff_and_ground_truth() -> None:
    [instance] = aacr.convert([RECORD], fake_fetch)

    assert instance.id == "FreeCAD__FreeCAD@a050e42"  # AACR's own id scheme
    assert instance.repo == "FreeCAD/FreeCAD"
    assert instance.language == "c++"
    assert instance.base_sha == RECORD["source_commit"]
    assert instance.head_sha == RECORD["target_commit"]
    assert instance.diff == "diff for FreeCAD/FreeCAD#19411"
    assert (instance.pr_title, instance.pr_body) == ("T", "B")

    first, second = instance.ground_truth  # blank text and missing path dropped
    assert (first.line_start, first.line_end) == (1123, 1126)  # reversed range fixed
    assert first.context_level == "file" and first.category == "Performance"
    assert (second.line_start, second.line_end) == (88, 88)  # one end missing
    assert second.context_level == "repo"


def test_records_without_a_pull_request_or_commits_and_duplicates_are_skipped() -> None:
    no_url = {**RECORD, "githubPrUrl": "https://example.com/x"}
    no_head = {**RECORD, "target_commit": ""}

    instances = aacr.convert([no_url, no_head, RECORD, RECORD], fake_fetch)

    assert [i.id for i in instances] == ["FreeCAD__FreeCAD@a050e42"]


def test_the_fetcher_uses_the_three_dot_compare_and_caches_both_calls(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "/compare/" in request.url.path:
            assert request.headers["Accept"] == "application/vnd.github.diff"
            assert request.headers["Authorization"] == "Bearer tok"
            return httpx.Response(200, content=b"diff --git a/x b/x\r\n+keep CRLF\r\n")
        return httpx.Response(200, json={"title": "Fix views", "body": None})

    fetcher = aacr.GitHubFetcher(tmp_path, token="tok")
    fetcher.client = httpx.Client(
        base_url="https://api.github.com",
        headers=fetcher.client.headers,
        transport=httpx.MockTransport(handler),
    )
    ref = aacr.parse_ref(RECORD)
    assert ref is not None

    first = fetcher(ref)
    second = fetcher(ref)

    assert calls == [
        f"/repos/FreeCAD/FreeCAD/compare/{RECORD['source_commit']}...{RECORD['target_commit']}",
        "/repos/FreeCAD/FreeCAD/pulls/19411",
    ]
    assert first == second
    assert first.diff == "diff --git a/x b/x\r\n+keep CRLF\r\n"
    assert (first.title, first.body) == ("Fix views", "")


def test_a_release_with_the_wrong_checksum_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def tampered(*_: Any, **__: Any) -> httpx.Response:
        return httpx.Response(200, content=b"[]", request=httpx.Request("GET", aacr.POSITIVE_URL))

    monkeypatch.setattr(aacr.httpx, "get", tampered)

    with pytest.raises(ValueError, match="checksum"):
        aacr.download_release(tmp_path / "positive_samples.json")


def test_a_pull_request_that_cannot_be_fetched_is_reported_and_left_out() -> None:
    other = {**RECORD, "target_commit": "b" * 40, "githubPrUrl": "https://github.com/o/gone/pull/1"}
    failures: list[str] = []

    def flaky(ref: aacr.PullRequestRef) -> aacr.PullRequestData:
        if ref.name == "gone":
            raise RuntimeError("404")
        return fake_fetch(ref)

    instances = aacr.convert(
        [other, RECORD], flaky, on_error=lambda ref, _: failures.append(ref.repo)
    )

    assert [i.id for i in instances] == ["FreeCAD__FreeCAD@a050e42"]
    assert failures == ["o/gone"]
    with pytest.raises(RuntimeError):
        aacr.convert([other], flaky)
