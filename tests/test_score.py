import json
import tempfile
import unittest
from pathlib import Path
from cdb_score import build as build_mod
from cdb_score import spec
from cdb_score.score import retention, score_runs, summarize
from cdb_score.validate import validate_package


def make_runs(mod=None):
    """Synthetic complete matrix; `mod(metrics, scenario, severity)` mutates a copy of the baseline."""
    base = {
        "safety": {"collision_count": 0, "min_ttc_s": 3.0, "ttc_applicable": True, "lane_invasion_count": 4,
                   "mrm_event_count": 1},
        "event_rates": {"ttc_warning": {"per_km": 8.0}, "hard_braking": {"per_km": 50.0},
                        "severe_braking": {"per_km": 20.0}},
        "comfort": {"max_longitudinal_jerk_mps3": 100.0, "max_jerk_vector_magnitude_mps3": 120.0,
                    "max_yaw_acceleration_radps2": 0.5, "max_yaw_rate_radps": 0.2,
                    "max_lateral_acceleration_mps2": 2.0, "min_longitudinal_acceleration_mps2": -6.0},
        "task_completion": {"completion_time_s": 120.0, "no_progress_duration_s": 6.0, "blocked_events": 0,
                            "route_completion": 0.99},
        "degradation": {"applied": True, "applied_sim_time_s": 20.0},
    }
    import copy
    runs = []
    for s in spec.SCENARIOS:
        for sv in spec.SEVERITIES:
            for rep in range(1, 6):
                m = copy.deepcopy(base)
                if mod:
                    mod(m, s.slug, sv)
                runs.append({"run_id": f"{s.slug}-{sv}-{rep}", "scenario": s.slug, "severity": sv,
                             "repeat": rep, "metrics": m})
    return runs


ENTRY = {"id": "test-org_detector_baseline", "model": "x", "creator": "x", "detector": "x",
         "fusion_mode": "lidar", "controller_profile": "baseline", "stack_version": "x"}


def make_package(runs, **entry):
    return {"schema": "cdb-submission/1.0", "entry": {**ENTRY, **entry}, "runs": runs}


class RetentionTests(unittest.TestCase):
    def test_no_worsening_is_full_retention(self):
        m = spec.METRIC_BY_KEY["task_completion.completion_time_s"]
        self.assertEqual(retention(m, 120.0, 110.0), 1.0)   # faster is not penalised

    def test_worsening_scales(self):
        m = spec.METRIC_BY_KEY["task_completion.completion_time_s"]
        self.assertAlmostEqual(retention(m, 120.0, 135.0), 0.5)  # 15 s of a 30 s scale
        self.assertEqual(retention(m, 120.0, 200.0), 0.0)

    def test_higher_is_better(self):
        m = spec.METRIC_BY_KEY["safety.min_ttc_s"]
        self.assertAlmostEqual(retention(m, 3.0, 1.5), 0.5)
        self.assertEqual(retention(m, 3.0, 3.5), 1.0)


class MatrixTests(unittest.TestCase):
    def test_perfect_matrix_scores_100(self):
        s = score_runs(make_runs())
        for a in spec.AXES:
            self.assertAlmostEqual(s["axes"][a], 100.0)
        self.assertAlmostEqual(s["cdb_index"], 100.0)

    def test_collision_at_s3_zeroes_that_metric(self):
        def mod(m, sc, sv):
            if sc == "dynamic-cut-in" and sv == "S3":
                m["safety"]["collision_count"] = 2
        s = score_runs(make_runs(mod))
        r = s["per_scenario"]["dynamic-cut-in"]["metrics"]["safety.collision_count"]["retention"]
        self.assertAlmostEqual(r, 0.5)  # S1,S2 intact (1,1), S3 zero with double weight -> (1+1+0)/4
        self.assertLess(s["axes"]["safety"], 100.0)
        self.assertAlmostEqual(s["axes"]["comfort"], 100.0)

    def test_monotone_worsening_orders_severities(self):
        def mod(m, sc, sv):
            m["task_completion"]["completion_time_s"] += {"S0": 0, "S1": 5, "S2": 10, "S3": 20}[sv]
        s = score_runs(make_runs(mod))
        rs = s["per_scenario"]["lead-slow"]["metrics"]["task_completion.completion_time_s"]["retention_by_severity"]
        self.assertGreater(rs["S1"], rs["S2"])
        self.assertGreater(rs["S2"], rs["S3"])

    def test_incomplete_matrix_is_partial_not_pass(self):
        v = validate_package(make_package(make_runs()[:-1]))
        self.assertEqual(v["status"], "PARTIAL")
        self.assertEqual(v["errors"], [])
        self.assertTrue(any("unique repeats" in e for e in v["coverage"]))

    def test_bootstrap_ci_brackets_point(self):
        s = summarize(make_runs(), with_ci=True)
        lo, hi = s["ci95"]["cdb_index"]
        self.assertLessEqual(lo, s["scores"]["cdb_index"])
        self.assertGreaterEqual(hi, s["scores"]["cdb_index"])


class SubmissionFlowTests(unittest.TestCase):
    """Validator statuses, generic packager, and the submission-driven news/date."""

    def test_full_matrix_passes(self):
        self.assertEqual(validate_package(make_package(make_runs()))["status"], "PASS")

    def test_malformed_packages_fail(self):
        self.assertEqual(validate_package({**make_package(make_runs()), "schema": "other"})["status"], "FAIL")
        self.assertEqual(validate_package(make_package(make_runs(), id="Bad ID!"))["status"], "FAIL")
        self.assertEqual(validate_package(make_package(make_runs(), fusion_mode="radar"))["status"], "FAIL")
        runs = make_runs()
        runs[0]["source_sha256"] = "not-a-hash"
        self.assertEqual(validate_package(make_package(runs))["status"], "FAIL")
        runs = make_runs()
        del runs[3]["metrics"]["comfort"]["max_yaw_rate_radps"]
        self.assertEqual(validate_package(make_package(runs))["status"], "FAIL")

    def test_package_from_run_dirs_round_trip(self):
        def dump(d):
            return "".join(f"{k}: {v}\n" for k, v in d.items())
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = [r for r in make_runs() if r["scenario"] == "lead-slow" and r["repeat"] == 1]
            for r in src:
                d = root / r["run_id"]
                (d / "metrics").mkdir(parents=True)
                fm = {**r["metrics"], "status": "COMPLETE", "degradation": {**r["metrics"]["degradation"], "severity": r["severity"]}}
                (d / "metrics" / "full_metrics.json").write_text(json.dumps(fm))
                (d / "manifest.yaml").write_text(dump({
                    "run_id": r["run_id"], "status": "COMPLETE", "replicate": 1,
                    "cdb_slot_id": f"lead-slow__{r['severity']}__r01"}))
            rej = root / "rejected_run"
            (rej / "metrics").mkdir(parents=True)
            (rej / "metrics" / "full_metrics.json").write_text("{}")
            (rej / "manifest.yaml").write_text(dump({"run_id": "rejected_run", "status": "REJECTED"}))
            pkg = build_mod.package_from_run_dirs(ENTRY, root)
        self.assertEqual(len(pkg["runs"]), 4)
        self.assertEqual(sorted(r["severity"] for r in pkg["runs"]), ["S0", "S1", "S2", "S3"])
        self.assertTrue(all(len(r["source_sha256"]) == 64 for r in pkg["runs"]))
        self.assertEqual(len(pkg["provenance"]["skipped"]), 1)
        v = validate_package(pkg)
        self.assertEqual(v["status"], "PARTIAL")
        s = summarize(pkg["runs"], with_ci=False)
        self.assertIsNotNone(s["per_scenario"]["lead-slow"]["axes"]["safety"])
        # the raw scorer averages whatever scenarios exist; the build layer must withhold that headline
        lb = self._build([{**ENTRY, "status": "real", "submitted_at": "2026-10-01T00:00:00Z"}], {ENTRY["id"]: pkg})
        row = lb["entries"][0]
        self.assertTrue(row["partial"])
        self.assertIsNone(row["scores"])
        self.assertNotIn("100.00", lb["updates"][0]["text"])

    def test_packager_accepts_gate_outcomes_and_pipeline_slugs(self):
        def dump(d):
            return "".join(f"{k}: {v}\n" for k, v in d.items())
        base = make_runs()[0]["metrics"]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i, (slot, status) in enumerate([("pedestrian-emerge-behind-parked-vehicle__S0__r01", "COMPLETE"),
                                                ("construction-obstacle__S1__r02", "VALID_FAILURE"),
                                                ("lead-stopped__S2__r03", "VALID_CONTROLLED_STOP"),
                                                ("lead-slow__S3__r01", "INVALID_STARTUP")]):
                d = root / f"run{i}"
                (d / "metrics").mkdir(parents=True)
                (d / "metrics" / "full_metrics.json").write_text(json.dumps(base))
                (d / "manifest.yaml").write_text(dump({"run_id": f"run{i}", "status": status, "supplementary_slot_id": slot}))
            pkg = build_mod.package_from_run_dirs(ENTRY, root)
        self.assertEqual([(r["scenario"], r["severity"], r["repeat"]) for r in pkg["runs"]],
                         [("pedestrian-emerge", "S0", 1), ("construction-obstacle", "S1", 2), ("lead-stopped", "S2", 3)])
        self.assertEqual(pkg["provenance"]["skipped"], ["run3: status INVALID_STARTUP"])

    def _build(self, entries, packages):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "subs").mkdir()
            for eid, pkg in packages.items():
                (td / "subs" / eid).mkdir()
                (td / "subs" / eid / "package.json").write_text(json.dumps(pkg))
            (td / "models.json").write_text(json.dumps({"entries": entries}))
            return build_mod.build_leaderboard(td / "models.json", td / "subs", td / "lb.json", with_ci=False)

    def test_news_and_date_come_only_from_submissions(self):
        full = make_package(make_runs(), id="org_full_baseline")
        part = make_package([r for r in make_runs() if r["scenario"] == "lead-slow" and r["repeat"] == 1],
                            id="org_part_baseline")
        entries = [
            {**full["entry"], "status": "real", "submitted_at": "2026-10-01T10:00:00Z"},
            {**part["entry"], "status": "real", "submitted_at": "2026-10-05T09:00:00Z"},
            {**ENTRY, "id": "org_pending_baseline", "status": "pending"},
        ]
        lb = self._build(entries, {"org_full_baseline": full, "org_part_baseline": part})
        self.assertEqual(lb["last_submission_utc"], "2026-10-05T09:00:00Z")
        self.assertNotIn("generated_at_utc", lb)
        self.assertEqual([u["entry_id"] for u in lb["updates"]], ["org_part_baseline", "org_full_baseline"])
        rows = {e["id"]: e for e in lb["entries"]}
        self.assertEqual(rows["org_full_baseline"]["rank"], 1)
        self.assertTrue(rows["org_part_baseline"]["partial"])
        self.assertIsNone(rows["org_part_baseline"]["scores"])
        self.assertNotIn("rank", rows["org_part_baseline"])

    def test_no_submissions_means_no_news_and_no_date(self):
        lb = self._build([{**ENTRY, "status": "pending"}], {})
        self.assertEqual(lb["updates"], [])
        self.assertIsNone(lb["last_submission_utc"])

    def test_build_refuses_unregistered_or_missing_packages(self):
        with self.assertRaises(build_mod.BuildError):
            self._build([], {"org_orphan_baseline": make_package(make_runs(), id="org_orphan_baseline")})
        with self.assertRaises(build_mod.BuildError):
            self._build([{**ENTRY, "status": "real"}], {})


class V11SpecTests(unittest.TestCase):
    """cdb-score/1.1: handling metrics belong to the Comfort & handling axis."""

    HANDLING = [
        "safety.mrm_event_count",
        "event_rates.hard_braking.per_km",
        "event_rates.severe_braking.per_km",
    ]

    def test_spec_version_and_axis_counts(self):
        self.assertEqual(spec.SPEC_VERSION, "cdb-score/1.1")
        self.assertEqual(len(spec.METRICS_BY_AXIS["safety"]), 4)
        self.assertEqual(len(spec.METRICS_BY_AXIS["comfort"]), 9)
        self.assertEqual(len(spec.METRICS_BY_AXIS["operation"]), 4)
        for key in self.HANDLING:
            self.assertEqual(spec.METRIC_BY_KEY[key].axis, "comfort")

    def test_reference_entry_v11_scores_and_intervals(self):
        pkg_path = (Path(__file__).resolve().parents[1] /
                    "submissions" / "autoware-0.3.8_bevfusion-lidar_baseline" / "package.json")
        if not pkg_path.exists():
            self.skipTest("reference submission package not present")
        pkg = json.loads(pkg_path.read_text())
        s = summarize(pkg["runs"], with_ci=True)
        self.assertAlmostEqual(s["scores"]["cdb_index"], 88.9301, places=2)
        self.assertAlmostEqual(s["scores"]["safety"], 89.6731, places=2)
        self.assertAlmostEqual(s["scores"]["comfort"], 78.7638, places=2)
        self.assertAlmostEqual(s["scores"]["operation"], 98.3533, places=2)
        ci = s["ci95"]
        self.assertAlmostEqual(ci["cdb_index"][0], 84.5296, places=2)
        self.assertAlmostEqual(ci["cdb_index"][1], 91.7467, places=2)
        self.assertAlmostEqual(ci["safety"][0], 83.0857, places=2)
        self.assertAlmostEqual(ci["safety"][1], 95.7481, places=2)
        self.assertAlmostEqual(ci["comfort"][0], 70.7257, places=2)
        self.assertAlmostEqual(ci["comfort"][1], 84.4369, places=2)
        self.assertAlmostEqual(ci["operation"][0], 95.6216, places=2)
        self.assertAlmostEqual(ci["operation"][1], 98.6547, places=2)

    def test_build_is_deterministic(self):
        registry = Path(__file__).resolve().parents[1] / "registry" / "models.json"
        submissions = Path(__file__).resolve().parents[1] / "submissions"
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a.json"
            b = Path(td) / "b.json"
            la = build_mod.build_leaderboard(registry, submissions, a, with_ci=True)
            lb = build_mod.build_leaderboard(registry, submissions, b, with_ci=True)
        self.assertEqual(la["spec_version"], "cdb-score/1.1")
        self.assertEqual(json.dumps(la, sort_keys=True), json.dumps(lb, sort_keys=True))
        self.assertEqual(la["metrics"], lb["metrics"])
        self.assertEqual(la["matrix"], lb["matrix"])


if __name__ == "__main__":
    unittest.main()
