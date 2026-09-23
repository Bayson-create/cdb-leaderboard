"""python -m cdb_score {validate|score|package|build|import-formal28|demo-package} ...

Exit codes: 0 = PASS / ok, 3 = PARTIAL (well-formed but incomplete matrix), 1 = FAIL, 2 = usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import build as build_mod
from .score import summarize
from .validate import load_package, validate_package

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cdb_score")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate", help="validate a submission package (dir or package.json)")
    p.add_argument("package")

    p = sub.add_parser("score", help="validate and score a submission package")
    p.add_argument("package")
    p.add_argument("--no-ci", action="store_true", help="skip the bootstrap interval")
    p.add_argument("--out", help="write the full result JSON here")

    p = sub.add_parser("package", help="build a cdb-submission/1.0 package from bench run directories")
    p.add_argument("--runs", required=True, help="folder holding <run_id>/manifest.yaml + metrics/full_metrics.json")
    p.add_argument("--entry", required=True, help="JSON file with the entry block (id, model, creator, ...)")
    p.add_argument("--out", required=True, help="output package.json")

    p = sub.add_parser("import-formal28", help="create the real Autoware/BEVFusion package from the evidence snapshot")
    p.add_argument("--formal28", default=str(build_mod.FORMAL28))
    p.add_argument("--entry-id", default="autoware-0.3.8_bevfusion-lidar_baseline")

    p = sub.add_parser("build", help="build docs/data/leaderboard.json from registry + submissions")
    p.add_argument("--registry", default=str(ROOT / "registry" / "models.json"))
    p.add_argument("--submissions", default=str(ROOT / "submissions"))
    p.add_argument("--out", default=str(ROOT / "docs" / "data" / "leaderboard.json"))
    p.add_argument("--no-ci", action="store_true")

    p = sub.add_parser("demo-package", help="write an empty package template with the required structure")
    p.add_argument("out")

    a = ap.parse_args(argv)

    if a.cmd == "validate":
        v = validate_package(load_package(Path(a.package)))
        print(json.dumps(v, indent=1))
        return {"PASS": 0, "PARTIAL": 3}.get(v["status"], 1)

    if a.cmd == "score":
        pkg = load_package(Path(a.package))
        v = validate_package(pkg)
        if v["status"] == "FAIL":
            print(json.dumps(v, indent=1))
            return 1
        if v["status"] == "PARTIAL":
            s = summarize(pkg["runs"], with_ci=False)
            brief = {"status": "PARTIAL", "note": "incomplete matrix: per-scenario scores only, no CDB total, not ranked",
                     "cells_complete": f'{v["n_cells_complete"]}/{v["n_cells_total"]}', "n_runs": s["n_runs"],
                     "per_scenario_axes": {k: v2["axes"] for k, v2 in s["per_scenario"].items()
                                           if any(x is not None for x in v2["axes"].values())}}
            print(json.dumps(brief, indent=1))
            if a.out:
                Path(a.out).write_text(json.dumps({"validation": v, **s}, indent=1))
            return 3
        s = summarize(pkg["runs"], with_ci=not a.no_ci)
        brief = {"scores": s["scores"], "ci95": s["ci95"], "n_runs": s["n_runs"],
                 "per_scenario_axes": {k: v2["axes"] for k, v2 in s["per_scenario"].items()}}
        print(json.dumps(brief, indent=1))
        if a.out:
            Path(a.out).write_text(json.dumps(s, indent=1))
        return 0

    if a.cmd == "package":
        entry = json.loads(Path(a.entry).read_text())
        pkg = build_mod.package_from_run_dirs(entry, Path(a.runs))
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(pkg, indent=1), encoding="utf-8")
        v = validate_package(pkg)
        print(f"wrote {a.out}  runs={len(pkg['runs'])}  skipped={len(pkg['provenance']['skipped'])}  "
              f"validation={v['status']}  cells={v['n_cells_complete']}/{v['n_cells_total']}")
        for e in v["errors"][:20]:
            print("  error:", e)
        return {"PASS": 0, "PARTIAL": 3}.get(v["status"], 1)

    if a.cmd == "import-formal28":
        registry = json.loads((ROOT / "registry" / "models.json").read_text())
        entry = next(e for e in registry["entries"] if e["id"] == a.entry_id)
        entry_meta = {k: entry[k] for k in ("id", "model", "creator", "detector", "fusion_mode",
                                             "controller_profile", "stack_version")}
        entry_meta["commit"] = entry.get("commit")
        pkg = build_mod.package_from_formal28(entry_meta, Path(a.formal28))
        out_dir = ROOT / "submissions" / a.entry_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "package.json").write_text(json.dumps(pkg, indent=1), encoding="utf-8")
        v = validate_package(pkg)
        print(f"wrote {out_dir/'package.json'}  runs={len(pkg['runs'])}  validation={v['status']}")
        return 0 if v["status"] == "PASS" else 1

    if a.cmd == "build":
        try:
            lb = build_mod.build_leaderboard(Path(a.registry), Path(a.submissions), Path(a.out), with_ci=not a.no_ci)
        except build_mod.BuildError as exc:
            print(exc, file=sys.stderr)
            return 1
        for e in lb["entries"]:
            sc = e.get("scores")
            tag = "partial" if e.get("partial") else (e["status"] or "")
            print(f"{e['id']:45s} {tag:8s} " + (f"CDB {sc['cdb_index']:.1f}  S {sc['safety']:.1f}  C {sc['comfort']:.1f}  O {sc['operation']:.1f}" if sc else "--"))
        print(f"wrote {a.out}")
        return 0

    if a.cmd == "demo-package":
        from . import spec
        tmpl = {
            "schema": "cdb-submission/1.0",
            "entry": {"id": "<org>_<detector>_<controller>", "model": "<display name>", "creator": "<org>",
                      "detector": "<e.g. centerpoint>", "fusion_mode": "lidar|camera_lidar_fusion",
                      "controller_profile": "baseline", "stack_version": "<autoware image tag>", "commit": "<sha>"},
            "runs": [{"run_id": "<unique id>", "scenario": s.slug, "severity": sv, "repeat": rep,
                      "metrics": "<subset of metrics/full_metrics.json: " + ", ".join(spec.PACKAGE_METRIC_KEYS[:6]) + ", ...>"}
                     for s in spec.SCENARIOS[:1] for sv in spec.SEVERITIES for rep in (1,)],
            "provenance": {"evidence_snapshot": "<dir>", "hashes_verified": "<n/n>", "generated_at_utc": "<iso>"},
        }
        Path(a.out).write_text(json.dumps(tmpl, indent=1))
        print(f"wrote {a.out}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
