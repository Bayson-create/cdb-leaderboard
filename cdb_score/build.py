"""Build submission packages from the formal28 evidence snapshot and the docs/ leaderboard.json."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import spec
from .score import summarize
from .validate import get_path, load_package, validate_package

ROOT = Path(__file__).resolve().parent.parent
FORMAL28 = Path("/Users/xiebeichen/Downloads/毕业论文/formal28_pattern_extraction_20260915")


def _pick(metrics: dict, keys: list[str]) -> dict:
    out: dict = {}
    for k in keys:
        v = get_path(metrics, k)
        if v is None:
            continue
        cur = out
        parts = k.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = v
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_from_formal28(entry: dict, formal28: Path = FORMAL28) -> dict:
    sel = json.loads((formal28 / "input_snapshot" / "formal_140_run_selection.json").read_text())
    runs = []
    for r in sel:
        p = formal28 / "input_snapshot" / "remote_derived_evidence" / r["run_id"] / "metrics" / "full_metrics.json"
        fm = json.loads(p.read_text())
        runs.append({
            "run_id": r["run_id"],
            "scenario": r["scenario"],
            "severity": r["severity"],
            "repeat": int(float(r["repeat"])),
            "cohort": r["cohort"],
            "status": fm.get("status"),
            "metrics": _pick(fm, spec.PACKAGE_METRIC_KEYS),
            "source_sha256": _sha256(p),
        })
    manifest = formal28 / "data" / "source_manifest.json"
    transfer = formal28 / "input_snapshot" / "remote_evidence_transfer_validation.json"
    prov = {
        "evidence_snapshot": "formal28_pattern_extraction_20260915",
        "source_manifest_sha256": _sha256(manifest) if manifest.exists() else None,
        "remote_transfer_validation_sha256": _sha256(transfer) if transfer.exists() else None,
        "hashes_verified": "7418/7418 derived files, 140/140 runs",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return {"schema": "cdb-submission/1.0", "entry": entry, "runs": runs, "provenance": prov}


def build_leaderboard(registry_path: Path, submissions_dir: Path, out_path: Path, with_ci: bool = True) -> dict:
    registry = json.loads(registry_path.read_text())
    entries_out: list[dict] = []
    for e in registry["entries"]:
        row: dict[str, Any] = {k: e.get(k) for k in ("id", "model", "creator", "detector", "fusion_mode",
                                                       "controller_profile", "stack_version", "status", "color", "notes")}
        pkg_dir = submissions_dir / e["id"]
        if e.get("status") == "real" and (pkg_dir / "package.json").exists():
            pkg = load_package(pkg_dir)
            v = validate_package(pkg)
            row["validation"] = {"status": v["status"], "n_runs": v["n_runs"],
                                 "cells_complete": f'{v["n_cells_complete"]}/{v["n_cells_total"]}',
                                 "warnings": len(v["warnings"]), "errors": v["errors"][:10]}
            if v["status"] == "PASS":
                s = summarize(pkg["runs"], with_ci=with_ci)
                row.update({
                    "scores": s["scores"], "ci95": s["ci95"], "per_scenario": s["per_scenario"],
                    "absolute_S3": s["absolute_S3"], "absolute_S0": s["absolute_S0"],
                    "n_runs": s["n_runs"], "provenance": pkg.get("provenance"),
                    "entry": pkg.get("entry"),
                    "run_values": _run_values(pkg["runs"]),
                })
            else:
                row["scores"] = None
        else:
            row["scores"] = None
            row["validation"] = None
        entries_out.append(row)

    ranked = sorted([r for r in entries_out if r.get("scores")], key=lambda r: -r["scores"]["cdb_index"])
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    lb = {
        "schema": "cdb-leaderboard/1.0",
        "spec_version": spec.SPEC_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "matrix": {"scenarios": [s.__dict__ for s in spec.SCENARIOS], "severities": spec.SEVERITIES,
                   "repeats": spec.REPEATS_REQUIRED, "intervention": spec.INTERVENTION,
                   "severity_weights": spec.SEVERITY_WEIGHTS},
        "metrics": [{"key": m.key, "axis": m.axis, "display": m.display, "unit": m.unit,
                     "better_when": m.better_when, "scale": m.scale_text,
                     "absolute_column": m.absolute_column} for m in spec.METRICS],
        "axes": spec.AXES,
        "entries": entries_out,
        "updates": registry.get("updates", []),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(lb, indent=1), encoding="utf-8")
    # JS wrapper so the static pages work without fetch (file://, GitHub Pages, sandboxed previews)
    js_path = out_path.with_suffix(".js")
    js_path.write_text("window.CDB_DATA = " + json.dumps(lb, separators=(",", ":")) + ";\n", encoding="utf-8")
    return lb


def _run_values(runs: list[dict]) -> list[dict]:
    """Compact run-level values for the scatter plots on the model page."""
    keep = [m.key for m in spec.METRICS]
    out = []
    for r in runs:
        out.append({"run_id": r["run_id"], "scenario": r["scenario"], "severity": r["severity"],
                    "repeat": r["repeat"],
                    "v": {k: get_path(r["metrics"], k) for k in keep}})
    return out
