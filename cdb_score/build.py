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


def _run_record(run_id: str, scenario: str, severity: str, repeat: int, full_metrics: Path, **extra) -> dict:
    fm = json.loads(full_metrics.read_text())
    rec = {"run_id": run_id, "scenario": scenario, "severity": severity, "repeat": int(repeat)}
    rec.update({k: v for k, v in extra.items() if v is not None})
    rec.update({"status": fm.get("status"), "metrics": _pick(fm, spec.PACKAGE_METRIC_KEYS),
                "source_sha256": _sha256(full_metrics)})
    return rec


def package_from_formal28(entry: dict, formal28: Path = FORMAL28) -> dict:
    sel = json.loads((formal28 / "input_snapshot" / "formal_140_run_selection.json").read_text())
    runs = [_run_record(r["run_id"], r["scenario"], r["severity"], int(float(r["repeat"])),
                        formal28 / "input_snapshot" / "remote_derived_evidence" / r["run_id"] / "metrics" / "full_metrics.json",
                        cohort=r["cohort"])
            for r in sel]
    # key order kept identical to the published reference package
    runs = [{k: r[k] for k in ("run_id", "scenario", "severity", "repeat", "cohort", "status", "metrics", "source_sha256")}
            for r in runs]
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


_SCEN_RE = None
# Outcomes the acquisition gate accepts: the data are valid even when the car failed its task
# (VALID_FAILURE, e.g. stopping short of the construction obstacle) or ended in a controlled stop.
ACCEPTED_RUN_STATUSES = {"COMPLETE", "VALID_FAILURE", "VALID_CONTROLLED_STOP"}
_SLUG_ALIASES = {s.pipeline_slug: s.slug for s in spec.SCENARIOS}


def _identify_run(run_dir: Path, fm: dict, manifest: dict) -> tuple[str, str, int] | None:
    """(scenario, severity, repeat) of one run directory, from the manifest the bench writes."""
    import re
    global _SCEN_RE
    if _SCEN_RE is None:
        slugs = sorted(set(_SLUG_ALIASES) | {s.slug for s in spec.SCENARIOS}, key=len, reverse=True)
        _SCEN_RE = re.compile("|".join(re.escape(x) for x in slugs))
    slot = manifest.get("cdb_slot_id") or manifest.get("supplementary_slot_id")
    if slot and slot.count("__") == 2:
        sc, sv, rep = slot.split("__")
        return _SLUG_ALIASES.get(sc, sc), sv, int(rep.lstrip("r"))
    m = _SCEN_RE.search(str(manifest.get("scenario") or fm.get("scenario") or ""))
    sv = (fm.get("degradation") or {}).get("severity")
    rep = manifest.get("replicate")
    if m and sv and rep is not None:
        return _SLUG_ALIASES.get(m.group(0), m.group(0)), sv, int(rep)
    return None


def _manifest_scalars(text: str) -> dict:
    """Top-level `key: scalar` pairs of a run manifest.yaml (no third-party YAML dependency)."""
    out: dict = {}
    for line in text.splitlines():
        if not line or line[0] in " \t#-" or ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if not val:
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        elif val.lstrip("-").isdigit():
            val = int(val)
        out[key.strip()] = val
    return out


def package_from_run_dirs(entry: dict, runs_root: Path, provenance: dict | None = None) -> dict:
    """Package every <runs_root>/<run_id>/ that has metrics/full_metrics.json and manifest.yaml.

    Runs whose manifest status is not an accepted outcome are skipped (rejected attempts stay out of the package).
    """
    runs, skipped = [], []
    for run_dir in sorted(p for p in Path(runs_root).iterdir() if p.is_dir()):
        fm_path = run_dir / "metrics" / "full_metrics.json"
        man_path = run_dir / "manifest.yaml"
        if not fm_path.exists() or not man_path.exists():
            skipped.append(f"{run_dir.name}: no full_metrics.json or manifest.yaml")
            continue
        manifest = _manifest_scalars(man_path.read_text())
        if manifest.get("status") not in ACCEPTED_RUN_STATUSES:
            skipped.append(f"{run_dir.name}: status {manifest.get('status')}")
            continue
        ident = _identify_run(run_dir, json.loads(fm_path.read_text()), manifest)
        if ident is None:
            skipped.append(f"{run_dir.name}: cannot identify scenario/severity/repeat")
            continue
        runs.append(_run_record(manifest.get("run_id") or run_dir.name, *ident, fm_path))
    prov = {"runs_root": str(Path(runs_root).name), "n_packaged": len(runs), "skipped": skipped,
            "hashes": "source_sha256 per run = sha256(metrics/full_metrics.json)",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    prov.update(provenance or {})
    return {"schema": "cdb-submission/1.0", "entry": entry, "runs": runs, "provenance": prov}


class BuildError(RuntimeError):
    pass


def _news(entries: list[dict]) -> list[dict]:
    """One item per submitted entry, newest first. Never hand-written."""
    items = []
    for e in entries:
        if not e.get("submitted_at"):
            continue
        v = e.get("validation") or {}
        sc = e.get("scores") or {}
        if v.get("status") == "PASS" and sc.get("cdb_index") is not None:
            text = (f"{e['model']}: CDB {sc['cdb_index']:.2f} (Safety {sc['safety']:.2f}, "
                    f"Comfort & handling {sc['comfort']:.2f}, Operation {sc['operation']:.2f}), "
                    f"{v['n_runs']} runs, {v['cells_complete']} cells.")
        else:
            text = (f"{e['model']}: incomplete package, {v.get('n_runs', 0)} runs, {v.get('cells_complete', '0/28')} cells "
                    "complete. Shown per scenario only, not ranked.")
        items.append({"date": e["submitted_at"][:10], "title": "New submission scored", "text": text,
                      "entry_id": e["id"], "link": e.get("submission_url")})
    return sorted(items, key=lambda i: (i["date"], i["entry_id"]), reverse=True)


def build_leaderboard(registry_path: Path, submissions_dir: Path, out_path: Path, with_ci: bool = True) -> dict:
    registry = json.loads(registry_path.read_text())
    registered = {e["id"] for e in registry["entries"]}
    problems = [f"submissions/{d.name}/ has a package but no registry entry"
                for d in sorted(Path(submissions_dir).iterdir())
                if d.is_dir() and (d / "package.json").exists() and d.name not in registered]
    entries_out: list[dict] = []
    for e in registry["entries"]:
        row: dict[str, Any] = {k: e.get(k) for k in ("id", "model", "creator", "detector", "fusion_mode",
                                                       "controller_profile", "stack_version", "status", "color", "notes",
                                                       "submitted_at", "submission_url")}
        pkg_dir = submissions_dir / e["id"]
        row["scores"] = None
        row["validation"] = None
        if e.get("status") == "real":
            if not (pkg_dir / "package.json").exists():
                problems.append(f"registry entry {e['id']} is 'real' but submissions/{e['id']}/package.json is missing")
                entries_out.append(row)
                continue
            pkg = load_package(pkg_dir)
            v = validate_package(pkg)
            row["validation"] = {"status": v["status"], "n_runs": v["n_runs"],
                                 "cells_complete": f'{v["n_cells_complete"]}/{v["n_cells_total"]}',
                                 "warnings": len(v["warnings"]), "errors": v["errors"][:10],
                                 "coverage": v["coverage"][:40]}
            if v["status"] == "FAIL":
                problems.append(f"registry entry {e['id']}: package FAILS validation: {v['errors'][:3]}")
            elif v["status"] == "PASS":
                s = summarize(pkg["runs"], with_ci=with_ci)
                row.update({
                    "scores": s["scores"], "ci95": s["ci95"], "per_scenario": s["per_scenario"],
                    "absolute_S3": s["absolute_S3"], "absolute_S0": s["absolute_S0"],
                    "n_runs": s["n_runs"], "provenance": pkg.get("provenance"),
                    "entry": pkg.get("entry"),
                    "run_values": _run_values(pkg["runs"]),
                })
            else:  # PARTIAL: per-scenario view only, no headline number, never ranked
                s = summarize(pkg["runs"], with_ci=False)
                row.update({
                    "partial": True, "per_scenario": s["per_scenario"],
                    "absolute_S3": s["absolute_S3"], "absolute_S0": s["absolute_S0"],
                    "n_runs": s["n_runs"], "provenance": pkg.get("provenance"), "entry": pkg.get("entry"),
                    "run_values": _run_values(pkg["runs"]),
                })
        entries_out.append(row)
    if problems:
        raise BuildError("leaderboard build refused:\n  " + "\n  ".join(problems))

    ranked = sorted([r for r in entries_out if r.get("scores")], key=lambda r: -r["scores"]["cdb_index"])
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    submitted = sorted(e["submitted_at"] for e in registry["entries"] if e.get("submitted_at"))
    lb = {
        "schema": "cdb-leaderboard/1.1",
        "spec_version": spec.SPEC_VERSION,
        "last_submission_utc": submitted[-1] if submitted else None,
        "matrix": {"scenarios": [s.__dict__ for s in spec.SCENARIOS], "severities": spec.SEVERITIES,
                   "repeats": spec.REPEATS_REQUIRED, "intervention": spec.INTERVENTION,
                   "severity_weights": spec.SEVERITY_WEIGHTS},
        "metrics": [{"key": m.key, "axis": m.axis, "display": m.display, "unit": m.unit,
                     "better_when": m.better_when, "scale": m.scale_text,
                     "absolute_column": m.absolute_column} for m in spec.METRICS],
        "axes": spec.AXES,
        "entries": entries_out,
        "updates": _news(entries_out),
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
