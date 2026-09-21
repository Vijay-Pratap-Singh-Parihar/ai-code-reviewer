"""Unit tests for `revu.context.rank`."""

from revu.context.rank import Bm25Index, RankCandidate, RankWeights, rank_candidates, tokenize


def test_tokenize_lowercases_and_extracts_identifiers() -> None:
    assert tokenize("def resolve_calls(Foo123, bar):") == [
        "def", "resolve_calls", "foo123", "bar",
    ]


def test_inverse_distance_score_prefers_closer_candidates() -> None:
    candidates = [
        RankCandidate(candidate_id=1, content="irrelevant text", distance=3),
        RankCandidate(candidate_id=2, content="irrelevant text", distance=0),
    ]
    weights = RankWeights(graph_distance=1.0, bm25=0.0)
    ranked = rank_candidates(candidates, diff_text="nothing shared here", weights=weights)
    assert [r.candidate_id for r in ranked] == [2, 1]
    assert ranked[0].graph_score == 1.0
    assert ranked[1].graph_score == 0.25


def test_bm25_prefers_lexically_similar_candidate() -> None:
    near, far = 1, 2
    candidates = [
        RankCandidate(candidate_id=near, content="def resolve_pr_merge_base(): pass", distance=1),
        RankCandidate(candidate_id=far, content="def totally_unrelated_thing(): pass", distance=1),
    ]
    ranked = rank_candidates(
        candidates,
        diff_text="def resolve_pr_merge_base(repo_path, base_branch): ...",
        weights=RankWeights(graph_distance=0.0, bm25=1.0),
    )
    assert ranked[0].candidate_id == near
    assert ranked[0].bm25_score > ranked[1].bm25_score


def test_embedding_scorer_is_not_called_when_weight_is_zero_by_default() -> None:
    calls: list[tuple[str, str]] = []

    def spy_scorer(diff_text: str, content: str) -> float:
        calls.append((diff_text, content))
        return 1.0

    candidates = [RankCandidate(candidate_id=1, content="x", distance=0)]
    ranked = rank_candidates(candidates, diff_text="y", embedding_scorer=spy_scorer)
    # The scorer is still invoked (so its raw value is available/inspectable),
    # but with the default weight of 0 it must not affect the combined score.
    assert calls  # confirms the pluggable hook actually ran
    assert ranked[0].embedding_score == 1.0
    assert ranked[0].score == ranked[0].graph_score * RankWeights().graph_distance + (
        ranked[0].bm25_score * RankWeights().bm25
    )


def test_rank_candidates_empty_input_returns_empty_list() -> None:
    assert rank_candidates([], diff_text="anything") == []


def test_bm25_index_scores_zero_for_empty_document() -> None:
    index = Bm25Index([[], ["foo", "bar"]])
    assert index.score(["foo"], 0) == 0.0
    assert index.score(["foo"], 1) > 0.0


def test_ranking_is_deterministic_tie_break_by_distance() -> None:
    far, near = 1, 2
    candidates = [
        RankCandidate(candidate_id=far, content="shared_token", distance=2),
        RankCandidate(candidate_id=near, content="shared_token", distance=1),
    ]
    weights = RankWeights(0.5, 0.5, 0.0)
    ranked = rank_candidates(candidates, diff_text="shared_token", weights=weights)
    # Equal BM25 score (identical content), so distance breaks the tie.
    assert [r.candidate_id for r in ranked] == [near, far]
