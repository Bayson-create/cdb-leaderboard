"""Build docs/data/mechanism_robustness.json for the CDB leaderboard from the
robustness_analysis_20260916 package. Read-only on the source; writes one file."""
import csv, json, hashlib
from pathlib import Path
from collections import defaultdict

R = Path("/Users/xiebeichen/Downloads/毕业论文/ma-xie_vedgar_perception_degradation_main_analysis/results/robustness_analysis_20260916")
OUT = Path("/Users/xiebeichen/Downloads/毕业论文/cdb-leaderboard/docs/data/mechanism_robustness.json")

def rows(p):
    return list(csv.DictReader(open(R / p, encoding="utf-8")))

def f(x):
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None

AXIS_ORDER = ["spatial", "density", "temporal", "semantic"]

# ---- T1: RS summary per axis ----
t1 = rows("tables/T1_robustness_score_summary.csv")
rs_summary = {}
for r in t1:
    rs_summary[r["axis"]] = {
        "axis_label": r["axis_label"],
        "cohort_consistent": {
            "safe": f(r["RS_cohort_consistent | Safe"]),
            "comfort": f(r["RS_cohort_consistent | Comfort & handling"]),
            "operation": f(r["RS_cohort_consistent | Operation"]),
        },
        "pooled": {
            "safe": f(r["RS_pooled | Safe"]),
            "comfort": f(r["RS_pooled | Comfort & handling"]),
            "operation": f(r["RS_pooled | Operation"]),
        },
    }

# ---- T1b: RS by scenario ----
t1b = rows("tables/T1b_robustness_score_by_scenario.csv")
by_scenario = defaultdict(list)
for r in t1b:
    by_scenario[r["axis"]].append({
        "scenario": r["scenario"], "scenario_label": r["scenario_label"],
        "safe": f(r["RS Safe"]), "comfort": f(r["RS Comfort & handling"]), "operation": f(r["RS Operation"]),
    })

# ---- T3: mechanism register ----
t3 = rows("tables/T3_mechanism_register.csv")
mechanisms = []
for r in t3:
    mechanisms.append({
        "axis": r["axis"], "axis_label": r["axis_label"], "mechanism": r["mechanism"],
        "runs": int(r["runs"]), "scenarios": r["scenarios"], "severities": r["severity_labels"],
        "metrics": int(r["metrics"]), "rs_pooled_mean": f(r["RS_pooled_mean"]),
        "rs_cohort_consistent_mean": f(r["RS_cohort_consistent_mean"]),
    })

# ---- T0: severity native levels (only rows with a positive run count) ----
t0 = rows("tables/T0_severity_native_levels.csv")
ladder = []
for r in t0:
    if r["axis"] in ("", "control", "—", "-"):
        continue
    if not r["runs_in_analysis_base"] or int(r["runs_in_analysis_base"]) == 0:
        continue
    ladder.append({
        "axis": r["axis"], "mechanism": r["mechanism"], "severity": r["severity"],
        "native_level": r["native_level"], "native_unit": r["native_unit"],
        "runs": int(r["runs_in_analysis_base"]), "scenarios": r["scenarios"],
    })

# ---- breaking points: dedupe to one row per axis, scenario, mechanism, metric_class ----
bp_rows = rows("data/breaking_points.csv")
seen = {}
for r in bp_rows:
    key = (r["axis"], r["scenario"], r["mechanism"], r["metric_class"])
    if key in seen:
        continue
    seen[key] = {
        "axis": r["axis"], "scenario": r["scenario"], "mechanism": r["mechanism"],
        "metric_class": r["metric_class"],
        "wilson_high": f(r["wilson_high"]),
        "continuous_breaking_point": f(r["continuous_breaking_point"]) if r["continuous_breaking_point"] else None,
        "first_level_below_0.8": r["first_level_below_0.8"] or None,
        "absolute_breaking_point": r["absolute_breaking_point"] == "True",
        "baseline_breaking_point": r["baseline_breaking_point"] == "True",
    }
breaking_points = sorted(seen.values(), key=lambda x: (x["axis"], x["scenario"], x["mechanism"], x["metric_class"]))

# ---- mediation correlations ----
med = rows("data/mediation_correlations.csv")
mediation = [{
    "mediator": r["mediator"], "axis": r["axis"], "cells": int(r["cells"]),
    "spearman_rho": f(r["spearman_rho"]), "p_value": f(r["p_value"]), "p_bh": f(r["p_bh"]),
} for r in med]

# ---- benchmark comparison ----
bench = rows("tables/T2_benchmark_comparison.csv")
benchmark = [{"dimension": r["dimension"], "perception_level": r["perception_level_benchmarks"],
              "this_benchmark": r["this_benchmark"]} for r in bench]

# ---- headline reconciliation (subset: axis-framework re-derivation rows + the two "cannot test" rows) ----
t6 = rows("tables/T6_headline_reconciliation.csv")
reconciliation = [{"claim": r["frozen_claim"], "thesis_value": r["frozen_thesis_value"],
                    "recomputed_value": r["frozen_recomputed_value"], "verdict": r["frozen_verdict"],
                    "note": r["frozen_note"]} for r in t6]

limitations = [
 "Severity is mechanism-local: three declared levels per mechanism (plus one continuous 8-level thinning ladder); the interpolated crossing is an estimate and cross-mechanism magnitude statements are not supported.",
 "Repeats are unequal: as few as n=3 in the semantic and delay cohorts; binary endpoints have a resolution of 20% or coarser; the ART interaction test is under-powered (no term reaches significance).",
 "Cohort comparability: route length, actor set-up and measurement windows differ between campaigns; pooled and cohort-consistent scores agree on which cells are fragile but not on absolute magnitude.",
 "The channel x lead-stopped ladder has no comparable nominal reference and is excluded from scoring.",
 "No noise/shape injector exists in the stack; the 'shape' axis in the original plan is not implemented and is substituted here by spatial coverage.",
 "Mediator coverage: perception recall exists for three scenarios, localisation error for four; the responsibility split is a readiness statement until the Oracle runs (see run_matrix.csv / oracle_matrix.csv) are executed.",
 "213 of 508 analysis-base runs have a local full_metrics.json; the rest come from cohort-level per-run exports, hashed and inventoried but one step further from the raw bag.",
 "Bounded to this simulator, this Autoware configuration (NDT localisation, CenterPoint/BEVFusion detection, camera for traffic lights) and the Town10 route family; nothing here transfers automatically to another stack or to road vehicles.",
 "construction-obstacle and dynamic-cut-in have 20 runs each, no accepted formal gate, and are treated as context-commissioning evidence.",
 "Route completion at the ceiling, zero-variance baselines and non-monotone metrics (NDT score, yaw rate) are reported as measured changes, never as safety statements.",
]

readme_text = (R / "README.md").read_text(encoding="utf-8")

data = {
    "schema": "cdb-mechanism-robustness/1.0",
    "source_package": "robustness_analysis_20260916",
    "source_readme_sha256": hashlib.sha256(readme_text.encode()).hexdigest(),
    "axis_order": AXIS_ORDER,
    "rs_summary": rs_summary,
    "rs_by_scenario": dict(by_scenario),
    "mechanisms": mechanisms,
    "severity_ladder": ladder,
    "breaking_points": breaking_points,
    "mediation": mediation,
    "benchmark_comparison": benchmark,
    "headline_reconciliation": reconciliation,
    "limitations": limitations,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
js = OUT.with_suffix(".js")
js.write_text("window.CDB_MECHANISMS = " + json.dumps(data, separators=(",", ":"), ensure_ascii=False) + ";\n", encoding="utf-8")
print("wrote", OUT, OUT.stat().st_size, "bytes")
print("axes:", list(rs_summary.keys()))
print("mechanisms:", len(mechanisms), "ladder rows:", len(ladder), "breaking points:", len(breaking_points), "mediation:", len(mediation))
