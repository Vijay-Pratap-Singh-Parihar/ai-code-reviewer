"""Benchmark harness for revu.

Implements the evaluation methodology in `docs/research/literature-review.md`
§10: a canonical dataset format with a committed dev/test split, matching of
predicted findings to ground-truth review comments, issue-level metrics with
recall by context level (Diff / File / Repo), calibration, cost and latency,
and paired statistics across configurations (bootstrap confidence intervals,
Wilcoxon signed-rank with Holm correction, Cliff's delta).

The unit of analysis is the pull request: every metric is computed per PR
first and aggregated afterwards, so statistics resample PRs, not comments.
"""
