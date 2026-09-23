"""Validate a CDB submission package before scoring."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import spec

REQUIRED_ENTRY_FIELDS = ["id", "model", "creator", "detector", "fusion_mode", "controller_profile", "stack_version"]
SCHEMA = "cdb-submission/1.0"
FUSION_MODES = {"lidar", "camera_lidar_fusion", "camera"}
ENTRY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def get_path(d: dict, dotted: str, default=None):
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def load_package(path: Path) -> dict:
    p = Path(path)
    if p.is_dir():
        p = p / "package.json"
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def validate_package(pkg: dict) -> dict:
    """Return {"status": "PASS"|"PARTIAL"|"FAIL", "errors": [...], "coverage": [...], "warnings": [...], "cells": {...}}.

    PASS    : every run is well formed and all 28 cells hold the required repeats.
    PARTIAL : every run is well formed, but some cells are missing or short (listed in "coverage").
              Scored per scenario only; never ranked.
    FAIL    : any malformed entry, run or metric.
    """
    errors: list[str] = []
    coverage: list[str] = []
    warnings: list[str] = []

    if pkg.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA!r} (got {pkg.get('schema')!r})")
    entry = pkg.get("entry") or {}
    for f in REQUIRED_ENTRY_FIELDS:
        if not entry.get(f):
            errors.append(f"entry.{f} is missing")
    if entry.get("id") and not ENTRY_ID_RE.match(str(entry["id"])):
        errors.append("entry.id must be 3-80 chars of lowercase letters, digits, '.', '_' or '-'")
    if entry.get("fusion_mode") and entry["fusion_mode"] not in FUSION_MODES:
        errors.append(f"entry.fusion_mode must be one of {sorted(FUSION_MODES)}")

    runs = pkg.get("runs") or []
    if not runs:
        errors.append("no runs in package")

    cells: dict[tuple[str, str], set[str]] = defaultdict(set)
    seen_run_ids: set[str] = set()
    for i, r in enumerate(runs):
        rid = r.get("run_id") or f"<run #{i}>"
        if rid in seen_run_ids:
            errors.append(f"duplicate run_id {rid}")
        seen_run_ids.add(rid)
        sc = r.get("scenario")
        sv = r.get("severity")
        if sc not in spec.SCENARIO_BY_SLUG:
            errors.append(f"{rid}: unknown scenario {sc!r}")
            continue
        if sv not in spec.SEVERITIES:
            errors.append(f"{rid}: unknown severity {sv!r}")
            continue
        sha = r.get("source_sha256")
        if sha is not None and not SHA256_RE.match(str(sha)):
            errors.append(f"{rid}: source_sha256 is not a 64-character hex digest")
        rep = str(r.get("repeat"))
        cells[(sc, sv)].add(rep)
        m = r.get("metrics") or {}
        for metric in spec.METRICS:
            v = get_path(m, metric.key)
            if v is None:
                if metric.requires and not get_path(m, metric.requires, False):
                    continue
                if metric.optional:
                    warnings.append(f"{rid}: optional metric {metric.key} is null (excluded)")
                    continue
                errors.append(f"{rid}: metric {metric.key} missing")
            elif not isinstance(v, (int, float)) or isinstance(v, bool):
                errors.append(f"{rid}: metric {metric.key} is not numeric ({v!r})")
        # intervention provenance
        applied = get_path(m, "degradation.applied")
        applied_t = get_path(m, "degradation.applied_sim_time_s")
        if sv != "S0":
            if applied is not True:
                errors.append(f"{rid}: degradation.applied must be true for {sv}")
            if applied_t is None:
                errors.append(f"{rid}: degradation.applied_sim_time_s missing for {sv}")
        if get_path(m, "safety.ttc_applicable") is False:
            warnings.append(f"{rid}: TTC not applicable, minimum TTC excluded from score")

    # matrix completeness
    for s in spec.SCENARIOS:
        for sv in spec.SEVERITIES:
            n = len(cells.get((s.slug, sv), ()))
            if n < spec.REPEATS_REQUIRED:
                coverage.append(f"cell {s.slug}/{sv}: {n} unique repeats, {spec.REPEATS_REQUIRED} required")
            elif n > spec.REPEATS_REQUIRED:
                warnings.append(f"cell {s.slug}/{sv}: {n} repeats (extra repeats are used)")

    cell_table = {f"{k[0]}/{k[1]}": len(v) for k, v in sorted(cells.items())}
    return {
        "status": "FAIL" if errors else ("PARTIAL" if coverage else "PASS"),
        "n_runs": len(runs),
        "n_cells_complete": sum(1 for v in cells.values() if len(v) >= spec.REPEATS_REQUIRED),
        "n_cells_total": len(spec.SCENARIOS) * len(spec.SEVERITIES),
        "errors": errors,
        "coverage": coverage,
        "warnings": warnings,
        "cells": cell_table,
    }
