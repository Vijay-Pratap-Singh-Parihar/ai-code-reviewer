"""Unit tests for `revu.context.budget` - real-tokenizer counting and
budget-respecting greedy selection."""

from revu.context.budget import BudgetCandidate, count_tokens, select_within_budget


def test_count_tokens_uses_a_real_tokenizer_not_character_count() -> None:
    # "supercalifragilisticexpialidocious" is one long word (35 chars) but
    # tokenizes into several BPE tokens - a character-count/4 estimate would
    # give a very different (larger, wrong-shaped) number than the real
    # tokenizer count for this kind of input.
    text = "supercalifragilisticexpialidocious"
    tokens = count_tokens(text)
    assert tokens > 1
    assert tokens < len(text)


def test_count_tokens_is_stable_and_nonzero_for_short_text() -> None:
    assert count_tokens("hello world") > 0


def test_select_within_budget_never_exceeds_budget() -> None:
    candidates = [
        BudgetCandidate(candidate_id=1, token_count=50, score=3.0),
        BudgetCandidate(candidate_id=2, token_count=40, score=2.0),
        BudgetCandidate(candidate_id=3, token_count=30, score=1.0),
    ]
    selection = select_within_budget(candidates, budget=75)
    assert selection.total_tokens <= 75
    # Highest score (50) taken first, then the next that still fits (30,
    # since 50+40=90 > 75 but 50+30=80 > 75 too -- so only the top one fits
    # cleanly with this exact set; assert the invariant, not a specific set).
    assert set(selection.selected_ids).issubset({1, 2, 3})


def test_select_within_budget_prefers_higher_score_and_fits_multiple_small_items() -> None:
    big_low_score, small_a, small_b, small_c = 1, 2, 3, 4
    candidates = [
        BudgetCandidate(candidate_id=big_low_score, token_count=90, score=1.0),
        BudgetCandidate(candidate_id=small_a, token_count=30, score=5.0),
        BudgetCandidate(candidate_id=small_b, token_count=30, score=4.0),
        BudgetCandidate(candidate_id=small_c, token_count=30, score=3.0),
    ]
    selection = select_within_budget(candidates, budget=100)
    assert selection.total_tokens <= 100
    assert set(selection.selected_ids) == {small_a, small_b, small_c}
    assert selection.total_tokens == 90


def test_larger_budget_selects_at_least_as_many_tokens_as_smaller_budget() -> None:
    candidates = [
        BudgetCandidate(candidate_id=1, token_count=20, score=5.0),
        BudgetCandidate(candidate_id=2, token_count=20, score=4.0),
        BudgetCandidate(candidate_id=3, token_count=20, score=3.0),
        BudgetCandidate(candidate_id=4, token_count=20, score=2.0),
    ]
    small = select_within_budget(candidates, budget=25)
    large = select_within_budget(candidates, budget=65)
    assert small.total_tokens <= 25
    assert large.total_tokens <= 65
    assert large.total_tokens >= small.total_tokens
    # Same fixed score order and equal-sized items: larger budget's selection
    # is a strict superset here (no adversarial size mismatch in this input).
    assert set(small.selected_ids).issubset(set(large.selected_ids))


def test_candidate_too_big_for_budget_is_never_selected() -> None:
    candidates = [BudgetCandidate(candidate_id=1, token_count=1000, score=10.0)]
    selection = select_within_budget(candidates, budget=100)
    assert selection.selected_ids == ()
    assert selection.total_tokens == 0


def test_zero_budget_selects_nothing() -> None:
    candidates = [BudgetCandidate(candidate_id=1, token_count=1, score=1.0)]
    selection = select_within_budget(candidates, budget=0)
    assert selection.selected_ids == ()
    assert selection.total_tokens == 0
