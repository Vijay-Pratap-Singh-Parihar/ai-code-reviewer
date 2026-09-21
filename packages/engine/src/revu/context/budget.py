"""Token-budgeted greedy knapsack selection over ranked candidates.

Costed with a real tokenizer (`tiktoken`'s `cl100k_base` encoding - the same
family LiteLLM's own cost accounting uses for OpenAI-shaped models) rather
than a character-count estimate, per the roadmap's explicit requirement.
`cl100k_base` is a reasonable fixed choice here: this stage doesn't know
which model will eventually consume the bundle (that's Stage 7's job to
decide), and token counts across modern tokenizers are close enough for
budgeting purposes that picking one consistently beats re-deriving it per
model for a component whose whole job is "roughly how much this will cost
to include," not exact accounting.

The selection itself is a **greedy** knapsack (highest score first, add
whenever it still fits, otherwise skip and keep considering smaller items
further down the list) - not an exact 0/1 knapsack solve. This matches the
roadmap's own wording ("greedy knapsack under token budget") and keeps
selection order legible (best-ranked-that-fits, in ranked order), but it is
a heuristic: it does not guarantee the maximum possible number of items or
total score for a given budget, and in adversarial score/size combinations
a smaller budget can occasionally select a different (not just smaller)
subset than a larger one would, rather than always producing a strict
subset - see `packages/engine/tests/context/test_budget.py` for the
monotonic-total behaviour this implementation is verified to have on
realistic inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import tiktoken

_ENCODING_NAME = "cl100k_base"


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding(_ENCODING_NAME)


def count_tokens(text: str) -> int:
    """Real token count via `tiktoken`, not `len(text) // 4` or similar."""
    return len(_encoding().encode(text))


@dataclass(frozen=True)
class BudgetCandidate:
    candidate_id: int
    token_count: int
    score: float


@dataclass(frozen=True)
class BudgetSelection:
    selected_ids: tuple[int, ...]
    total_tokens: int


def select_within_budget(
    candidates: list[BudgetCandidate], *, budget: int
) -> BudgetSelection:
    """Greedily select candidates (highest `score` first) that fit within
    `budget` total tokens. Never exceeds `budget`. A candidate whose own
    token count alone exceeds `budget` is simply never selected.
    """
    if budget < 0:
        raise ValueError("budget must be >= 0")

    ordered = sorted(candidates, key=lambda c: -c.score)
    selected: list[int] = []
    total = 0
    for candidate in ordered:
        if total + candidate.token_count <= budget:
            selected.append(candidate.candidate_id)
            total += candidate.token_count

    return BudgetSelection(selected_ids=tuple(selected), total_tokens=total)
