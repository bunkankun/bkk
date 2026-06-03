#!/usr/bin/env python3
"""Build a dev corpus by stratified random sampling from KRP and TLS sources."""
import random
import shutil
from collections import defaultdict
from pathlib import Path

KRP_SRC = Path("/home/chris/00scratch/bkk-work/output")
TLS_SRC = Path("/home/Shared/bkk/bkkbooks")
DEST = Path("/home/chris/00scratch/bkk-work/devcorpus")
KRP_N = 150
TLS_N = 50
SEED = 20260514


def stratified_sample(
    buckets: dict[str, list],
    target: int,
    rng: random.Random,
    cap: int | None = None,
) -> list:
    """Allocate `target` picks across buckets, then sample.

    If `target >= num_buckets`, each non-empty bucket gets at least 1.
    Otherwise, `target` buckets are picked at random (uniformly) and each
    gets 1. Remaining picks are allocated proportionally to bucket size
    via largest-remainder, optionally capped at `cap` per bucket.
    """
    sizes = {k: len(v) for k, v in buckets.items() if v}
    if target >= len(sizes):
        base = {k: 1 for k in sizes}
        remaining = target - len(sizes)
        active = list(sizes)
    else:
        active = rng.sample(sorted(sizes), target)
        base = {k: 1 for k in active}
        remaining = 0

    if remaining:
        active_total = sum(sizes[k] for k in active)
        raw = {k: remaining * sizes[k] / active_total for k in active}
        floors = {k: int(r) for k, r in raw.items()}
        order = sorted(active, key=lambda k: (raw[k] - floors[k], sizes[k]), reverse=True)
        need = remaining - sum(floors.values())
        for k in order[:need]:
            floors[k] += 1
    else:
        floors = {k: 0 for k in active}

    alloc = {}
    for k in active:
        n = base[k] + floors[k]
        if cap is not None:
            n = min(n, cap)
        alloc[k] = min(n, sizes[k])

    # if capping shrank the total, redistribute leftovers
    shortfall = target - sum(alloc.values())
    while shortfall > 0:
        # candidates that can still grow
        cands = [
            k for k in active
            if alloc[k] < sizes[k] and (cap is None or alloc[k] < cap)
        ]
        if not cands:
            break
        rng.shuffle(cands)
        # weight by remaining capacity
        for k in cands:
            if shortfall == 0:
                break
            alloc[k] += 1
            shortfall -= 1

    picks = []
    for section in sorted(alloc):
        n = alloc[section]
        if n <= 0:
            continue
        picks.extend(rng.sample(buckets[section], n))
    return picks


def main() -> None:
    rng = random.Random(SEED)

    # KRP: section dir contains text dirs
    krp_buckets: dict[str, list] = defaultdict(list)
    for section_dir in sorted(p for p in KRP_SRC.iterdir() if p.is_dir()):
        for text_dir in sorted(p for p in section_dir.iterdir() if p.is_dir()):
            krp_buckets[section_dir.name].append(text_dir)

    # TLS: flat dir of text dirs; section is first 4 chars of name
    tls_buckets: dict[str, list] = defaultdict(list)
    for text_dir in sorted(p for p in TLS_SRC.iterdir() if p.is_dir()):
        section = text_dir.name[:4]
        tls_buckets[section].append(text_dir)

    krp_total = sum(len(v) for v in krp_buckets.values())
    tls_total = sum(len(v) for v in tls_buckets.values())
    print(f"KRP: {krp_total} texts across {len(krp_buckets)} sections")
    print(f"TLS: {tls_total} texts across {len(tls_buckets)} sections")

    krp_pick = stratified_sample(krp_buckets, KRP_N, rng)
    # cap TLS per section so the very large KR3e bucket doesn't dominate
    tls_pick = stratified_sample(tls_buckets, TLS_N, rng, cap=3)
    print(f"Sampling {len(krp_pick)} KRP texts, {len(tls_pick)} TLS texts")

    krp_dest = DEST / "krp"
    tls_dest = DEST / "tls"
    krp_dest.mkdir(parents=True, exist_ok=True)
    tls_dest.mkdir(parents=True, exist_ok=True)

    for src in krp_pick:
        target = krp_dest / src.parent.name / src.name
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, target, symlinks=True)

    for src in tls_pick:
        target = tls_dest / src.name
        if target.exists():
            continue
        shutil.copytree(src, target, symlinks=True)

    # Summary by section
    krp_by_sec: dict[str, int] = defaultdict(int)
    for p in krp_pick:
        krp_by_sec[p.parent.name] += 1
    tls_by_sec: dict[str, int] = defaultdict(int)
    for p in tls_pick:
        tls_by_sec[p.name[:4]] += 1

    print("\nKRP picks per section:")
    for k in sorted(krp_by_sec):
        print(f"  {k}: {krp_by_sec[k]}")
    print("\nTLS picks per section:")
    for k in sorted(tls_by_sec):
        print(f"  {k}: {tls_by_sec[k]}")


if __name__ == "__main__":
    main()
