"""Score candidate context items by a configurable weighted combination of
signals.

Two signals are implemented:

- **Inverse graph distance** - a candidate reached in fewer traversal hops
  from a changed node scores higher (`1 / (1 + distance)`).
- **BM25** over the diff's own text vs. each candidate's source content -
  candidates that textually resemble what actually changed (shared
  identifiers, similar wording) score higher. Tokenisation is intentionally
  crude (`[A-Za-z_][A-Za-z0-9_]*`, lower-cased) - it treats `getUserById` and
  `get_user_by_id` as unrelated token sets, and matches on decorators,
  string literals, comments, and identifiers indiscriminately, since it
  doesn't parse the diff at all. This is a known, documented limitation:
  a real implementation would want at least camel/snake-case splitting and
  probably a code-aware tokenizer; plain regex tokenisation was judged good
  enough for a first cut given the ranking signal it's mixed with (graph
  distance) already carries most of the structural signal.

A third, **pluggable** scorer slot (`embedding_scorer`) exists for a future
code-embedding similarity signal, with a default weight of **0** - per this
stage's explicit constraint, no embedding model or API call is implemented
or faked here. The slot exists purely so a real implementation can be
dropped in later (Stage 7+) without reshaping this module's signature.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# (diff_text, candidate_content) -> similarity score, conventionally in
# [0, 1]. Not implemented in this stage - see module docstring. A caller
# that supplies one is responsible for its own model/network handling;
# `rank_candidates` never calls out to a network itself.
EmbeddingScorer = Callable[[str, str], float]


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class RankWeights:
    """Weights for the linear combination of scoring signals. Defaults were
    chosen, not tuned against any benchmark (none exists for this track -
    see `IMPLEMENTATION_PLAN.md` Stage 6): graph distance is weighted higher
    than BM25 on the reasoning that "is this code structurally connected to
    the change" is a stronger signal than "does this code share vocabulary
    with the diff" for a code graph that already has resolved call/import
    edges, but this is a judgment call, not a measured result.
    """

    graph_distance: float = 0.6
    bm25: float = 0.4
    embedding: float = 0.0


_DEFAULT_WEIGHTS = RankWeights()


@dataclass(frozen=True)
class RankCandidate:
    """One candidate to be scored. `candidate_id` is any hashable value the
    caller chooses to identify the candidate with (usually a graph node
    index, but `bundle.py` also mints synthetic ids for hunks that mapped to
    no graph node).
    """

    candidate_id: int
    content: str
    distance: int


@dataclass(frozen=True)
class RankedCandidate:
    candidate_id: int
    score: float
    graph_score: float
    bm25_score: float
    embedding_score: float
    distance: int


class Bm25Index:
    """A small, dependency-free BM25 implementation (Okapi BM25) over an
    already-tokenized corpus. Standalone rather than pulled from a library:
    the corpus here is always small (one candidate set per bundle build,
    typically tens of items), so there is no performance reason to reach for
    a heavier implementation, and this keeps tokenisation fully under this
    module's control for testing.
    """

    def __init__(self, documents: list[list[str]], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._documents = documents
        self._k1 = k1
        self._b = b
        self._doc_lengths = [len(doc) for doc in documents]
        self._avg_doc_length = (
            sum(self._doc_lengths) / len(self._doc_lengths) if documents else 0.0
        )
        self._doc_freq: dict[str, int] = {}
        for doc in documents:
            for term in set(doc):
                self._doc_freq[term] = self._doc_freq.get(term, 0) + 1
        self._n_docs = len(documents)

    def _idf(self, term: str) -> float:
        n_containing = self._doc_freq.get(term, 0)
        return math.log((self._n_docs - n_containing + 0.5) / (n_containing + 0.5) + 1)

    def score(self, query_tokens: list[str], document_index: int) -> float:
        doc = self._documents[document_index]
        if not doc or not query_tokens:
            return 0.0
        term_freqs = Counter(doc)
        doc_length = self._doc_lengths[document_index]
        length_norm = (
            1 - self._b + self._b * doc_length / self._avg_doc_length
            if self._avg_doc_length
            else 1.0
        )
        total = 0.0
        for term in query_tokens:
            freq = term_freqs.get(term, 0)
            if freq == 0:
                continue
            idf = self._idf(term)
            total += idf * (freq * (self._k1 + 1)) / (freq + self._k1 * length_norm)
        return total


def inverse_distance_score(distance: int) -> float:
    return 1.0 / (1 + distance)


@dataclass(frozen=True)
class _Normalized:
    graph: dict[int, float] = field(default_factory=dict)
    bm25: dict[int, float] = field(default_factory=dict)


def rank_candidates(
    candidates: list[RankCandidate],
    *,
    diff_text: str,
    weights: RankWeights = _DEFAULT_WEIGHTS,
    embedding_scorer: EmbeddingScorer | None = None,
) -> list[RankedCandidate]:
    """Score and sort `candidates` (highest score first; ties broken by
    ascending graph distance, then by input order).

    BM25 scores are max-normalised across the candidate set to [0, 1] before
    weighting, so `weights.bm25` and `weights.graph_distance` are on
    comparable scales (raw BM25 scores are unbounded and depend on corpus
    size, so summing them directly against the [0, 1] `graph_distance`
    signal without normalising would let corpus size silently dominate the
    combined score).
    """
    if not candidates:
        return []

    diff_tokens = tokenize(diff_text)
    bm25_index = Bm25Index([tokenize(c.content) for c in candidates])
    raw_bm25 = [bm25_index.score(diff_tokens, i) for i in range(len(candidates))]
    max_bm25 = max(raw_bm25) if raw_bm25 else 0.0

    ranked: list[RankedCandidate] = []
    for i, candidate in enumerate(candidates):
        graph_score = inverse_distance_score(candidate.distance)
        bm25_score = raw_bm25[i] / max_bm25 if max_bm25 > 0 else 0.0
        embedding_score = (
            embedding_scorer(diff_text, candidate.content) if embedding_scorer is not None else 0.0
        )
        combined = (
            weights.graph_distance * graph_score
            + weights.bm25 * bm25_score
            + weights.embedding * embedding_score
        )
        ranked.append(
            RankedCandidate(
                candidate_id=candidate.candidate_id,
                score=combined,
                graph_score=graph_score,
                bm25_score=bm25_score,
                embedding_score=embedding_score,
                distance=candidate.distance,
            )
        )

    ranked.sort(key=lambda r: (-r.score, r.distance))
    return ranked
