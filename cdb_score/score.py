"""CDB degradation-robustness score.

For every scenario q, metric m (axis a) and severity s in {S1,S2,S3}:

    b      = mean over S0 runs of m               (baseline)
    d_s    = mean over Ss runs of m
    w_s    = max(0, sign_m * (d_s - b))           (worsening; sign = +1 if lower is better)
    r_qms  = 1 - clip(w_s / scale_m(b), 0, 1)     (retention)
    r_qm   = (r_S1 + r_S2 + 2 r_S3) / 4           (S3 counts double)
    Axis_a = 100 * mean_{q,m in a} r_qm
    CDB    = mean of the three axes

Uncertainty: run-level bootstrap (resample runs within every scenario x severity cell) of the
whole pipeline; 95 % percentile interval.  The unit of analysis is one accepted run.
"""
from __future__ import annotations

import random
import statistics
from collections import defaultdict
from typing import Any

from . import spec
from .validate import get_path


def _value(metrics: dict, m: spec.Metric):
    if m.requires and not get_path(metrics, m.requires, False):
        return None
    v = get_path(metrics, m.key)
    if v is None or isinstance(v, bool):
        return None
    v = float(v)
    return m.transform(v) if m.transform else v


def _cells(runs: list[dict]) -> dict[tuple[str, str], list[dict]]:
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in runs:
        cells[(r["scenario"], r["severity"])].append(r)
    return cells


def _mean(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return statistics.fmean(vals) if vals else None


def retention(m: spec.Metric, baseline: float, degraded: float) -> float:
    sign = 1.0 if m.better_when == "lower" else -1.0
    w = max(0.0, sign * (degraded - baseline))
    scale = m.scale(baseline)
    if scale <= 0:
        return 1.0 if w == 0 else 0.0
    return 1.0 - min(1.0, w / scale)


def score_runs(runs: list[dict]) -> dict[str, Any]:
    """Deterministic point estimate. Returns the full breakdown."""
    cells = _cells(runs)
    per_scenario: dict[str, dict] = {}
    axis_pool: dict[str, list[float]] = {a: [] for a in spec.AXES}
    for sc in spec.SCENARIOS:
        base_runs = cells.get((sc.slug, "S0"), [])
        sc_out = {"axes": {}, "metrics": {}}
        axis_r: dict[str, list[float]] = {a: [] for a in spec.AXES}
        for m in spec.METRICS:
            b = _mean([_value(r["metrics"], m) for r in base_runs])
            levels = {"S0": b}
            r_levels = {}
            if b is None:
                sc_out["metrics"][m.key] = {"levels": levels, "retention": None, "note": "no baseline"}
                continue
            for sv in ("S1", "S2", "S3"):
                d = _mean([_value(r["metrics"], m) for r in cells.get((sc.slug, sv), [])])
                levels[sv] = d
                if d is not None:
                    r_levels[sv] = retention(m, b, d)
            if len(r_levels) != 3:
                sc_out["metrics"][m.key] = {"levels": levels, "retention": None, "note": "incomplete severities"}
                continue
            wsum = sum(spec.SEVERITY_WEIGHTS.values())
            r_qm = sum(spec.SEVERITY_WEIGHTS[sv] * r_levels[sv] for sv in r_levels) / wsum
            sc_out["metrics"][m.key] = {"levels": levels, "retention_by_severity": r_levels, "retention": r_qm}
            axis_r[m.axis].append(r_qm)
            axis_pool[m.axis].append(r_qm)
        for a in spec.AXES:
            sc_out["axes"][a] = 100.0 * statistics.fmean(axis_r[a]) if axis_r[a] else None
        per_scenario[sc.slug] = sc_out

    axes = {a: (100.0 * statistics.fmean(axis_pool[a]) if axis_pool[a] else None) for a in spec.AXES}
    valid = [v for v in axes.values() if v is not None]
    cdb = statistics.fmean(valid) if len(valid) == len(spec.AXES) else None
    return {"axes": axes, "cdb_index": cdb, "per_scenario": per_scenario}


def bootstrap(runs: list[dict], n: int = spec.BOOTSTRAP_N, seed: int = spec.BOOTSTRAP_SEED) -> dict[str, Any]:
    rng = random.Random(seed)
    cells = _cells(runs)
    keys = list(cells)
    samples: dict[str, list[float]] = {a: [] for a in spec.AXES}
    samples["cdb_index"] = []
    for _ in range(n):
        resampled: list[dict] = []
        for k in keys:
            pool = cells[k]
            resampled.extend(rng.choice(pool) for _ in range(len(pool)))
        s = score_runs(resampled)
        for a in spec.AXES:
            if s["axes"][a] is not None:
                samples[a].append(s["axes"][a])
        if s["cdb_index"] is not None:
            samples["cdb_index"].append(s["cdb_index"])

    def ci(xs: list[float]):
        if not xs:
            return None
        xs = sorted(xs)
        lo = xs[int(0.025 * (len(xs) - 1))]
        hi = xs[int(0.975 * (len(xs) - 1))]
        return [lo, hi]

    return {k: ci(v) for k, v in samples.items()}


def absolute_at(runs: list[dict], severity: str = "S3") -> dict[str, dict[str, float | None]]:
    """Mean of the 'absolute' columns at one severity, per scenario and overall (per-scenario mean)."""
    cells = _cells(runs)
    out: dict[str, dict[str, float | None]] = {"overall": {}}
    for m in spec.METRICS:
        if not m.absolute_column:
            continue
        per = []
        for sc in spec.SCENARIOS:
            v = _mean([_value(r["metrics"], m) for r in cells.get((sc.slug, severity), [])])
            out.setdefault(sc.slug, {})[m.key] = v
            if v is not None:
                per.append(v)
        out["overall"][m.key] = statistics.fmean(per) if per else None
    return out


def summarize(runs: list[dict], with_ci: bool = True) -> dict[str, Any]:
    point = score_runs(runs)
    result = {
        "spec_version": spec.SPEC_VERSION,
        "n_runs": len(runs),
        "scores": {
            "cdb_index": point["cdb_index"],
            **{a: point["axes"][a] for a in spec.AXES},
        },
        "ci95": bootstrap(runs) if with_ci else None,
        "per_scenario": point["per_scenario"],
        "absolute_S3": absolute_at(runs, "S3"),
        "absolute_S0": absolute_at(runs, "S0"),
    }
    return result
