"""Loading instances and making the committed dev/test split.

The canonical on-disk format is JSON Lines, one `BenchInstance` per line.
Adapters for each public benchmark convert its release into this format once,
and the converted file plus the split file are what experiments read.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from revu_bench.schema import BenchInstance, Split


def load_instances(path: Path) -> list[BenchInstance]:
    instances: list[BenchInstance] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                instances.append(BenchInstance.model_validate_json(line))
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: not a valid instance: {exc}") from exc
    ids = [i.id for i in instances]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"{path}: duplicate instance ids: {sorted(duplicates)[:5]}")
    return instances


def save_instances(path: Path, instances: Iterable[BenchInstance]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for instance in instances:
            handle.write(instance.model_dump_json() + "\n")


def fingerprint(instances: Sequence[BenchInstance]) -> str:
    """A short hash of the instance ids and their ground truth, recorded in
    the split file and in reports so a result can be tied to the exact data."""
    digest = hashlib.sha256()
    for instance in sorted(instances, key=lambda i: i.id):
        digest.update(instance.id.encode())
        digest.update(json.dumps([g.model_dump() for g in instance.ground_truth]).encode())
    return digest.hexdigest()[:16]


def make_split(
    instances: Sequence[BenchInstance], *, dev_size: int, seed: int = 20261008
) -> dict[str, Split]:
    """A deterministic dev/test split, stratified by language (§10.1): dev
    takes from each language in proportion to its share, so tuning prompts
    or thresholds on dev doesn't skew towards the biggest language. The same
    data and seed always give the same split."""
    if not 0 < dev_size < len(instances):
        raise ValueError(f"dev_size must be between 1 and {len(instances) - 1}")
    by_language: dict[str, list[str]] = defaultdict(list)
    for instance in sorted(instances, key=lambda i: i.id):
        by_language[instance.language].append(instance.id)

    rng = random.Random(seed)
    total = len(instances)
    quota = {lang: dev_size * len(ids) / total for lang, ids in by_language.items()}
    taken = {lang: int(q) for lang, q in quota.items()}
    # Hand out the remaining dev slots to the largest remainders.
    remaining = dev_size - sum(taken.values())
    for lang in sorted(quota, key=lambda lang: (quota[lang] - taken[lang], lang), reverse=True):
        if remaining == 0:
            break
        if taken[lang] < len(by_language[lang]):
            taken[lang] += 1
            remaining -= 1

    split: dict[str, Split] = {}
    for lang, ids in sorted(by_language.items()):
        shuffled = ids[:]
        rng.shuffle(shuffled)
        for position, instance_id in enumerate(shuffled):
            split[instance_id] = "dev" if position < taken[lang] else "test"
    return split


def save_split(path: Path, split: dict[str, Split], *, data_fingerprint: str, seed: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fingerprint": data_fingerprint, "seed": seed, "split": dict(sorted(split.items()))}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_split(path: Path) -> tuple[dict[str, Split], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["split"], payload["fingerprint"]


def select(
    instances: Sequence[BenchInstance], split: dict[str, Split], which: Split
) -> list[BenchInstance]:
    missing = [i.id for i in instances if i.id not in split]
    if missing:
        raise ValueError(f"{len(missing)} instances are not in the split, e.g. {missing[:3]}")
    return [i for i in instances if split[i.id] == which]
