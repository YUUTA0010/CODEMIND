"""Seeded statistics: percentile bootstrap CIs, paired bootstrap for differences and a paired sign-flip permutation test.

With few tasks these tools give wide intervals - which is the honest answer. `MIN_N_FOR_INFERENCE` flags small samples so
a table can never be read as evidence of improvement when n is tiny."""
from __future__ import annotations

import random
from statistics import mean

MIN_N_FOR_INFERENCE = 20


def bootstrap_ci(values: list[float], *, n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float, float]:
    """-> (mean, lo, hi). Percentile bootstrap over tasks. Degenerate for n<2 (lo == hi == mean)."""
    if not values:
        return (float("nan"),) * 3
    m = mean(values)
    if len(values) < 2:
        return m, m, m
    rng = random.Random(seed)
    n = len(values)
    means = sorted(mean(values[rng.randrange(n)] for _ in range(n)) for _ in range(n_boot))
    return m, means[int((alpha / 2) * n_boot)], means[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]


def paired_difference(a: list[float], b: list[float], *, n_boot: int = 4000, n_perm: int = 10000, seed: int = 0) -> dict:
    """Compare method A against B on the SAME tasks (a[i], b[i] belong to task i). Positive diff => A better."""
    if len(a) != len(b):
        raise ValueError("paired samples must have equal length")
    n = len(a)
    diffs = [x - y for x, y in zip(a, b)]
    if n == 0:
        return {"n": 0, "mean_diff": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "p_value": float("nan"), "wins": 0, "losses": 0, "ties": 0,
                "sufficient_n": False}
    md = mean(diffs)
    _, lo, hi = bootstrap_ci(diffs, n_boot=n_boot, seed=seed)
    wins, losses = sum(1 for d in diffs if d > 1e-12), sum(1 for d in diffs if d < -1e-12)
    nonzero = [d for d in diffs if abs(d) > 1e-12]
    if not nonzero:
        p = 1.0
    else:
        rng = random.Random(seed + 1)
        obs = abs(sum(nonzero))
        if len(nonzero) <= 14:  # exact enumeration
            cnt = 0
            total = 1 << len(nonzero)
            for mask in range(total):
                s = sum(d if (mask >> i) & 1 else -d for i, d in enumerate(nonzero))
                cnt += abs(s) >= obs - 1e-12
            p = cnt / total
        else:
            cnt = 0
            for _ in range(n_perm):
                s = sum(d if rng.random() < 0.5 else -d for d in nonzero)
                cnt += abs(s) >= obs - 1e-12
            p = (cnt + 1) / (n_perm + 1)
    return {"n": n, "mean_diff": md, "ci_low": lo, "ci_high": hi, "p_value": p, "wins": wins, "losses": losses, "ties": n - wins - losses,
            "sufficient_n": n >= MIN_N_FOR_INFERENCE}


def interpret(d: dict) -> str:
    """Plain-language reading of a paired comparison that refuses to over-claim."""
    if d["n"] == 0:
        return "no data"
    if not d["sufficient_n"]:
        return f"inconclusive: only n={d['n']} tasks (< {MIN_N_FOR_INFERENCE}); descriptive only"
    if d["ci_low"] > 0:
        return "improvement (95% CI excludes 0); check practical significance and multiple-comparison correction"
    if d["ci_high"] < 0:
        return "regression (95% CI excludes 0)"
    return "no detectable difference (95% CI includes 0)"
