import unittest
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

    def test_validator_requires_full_matrix(self):
        runs = make_runs()
        v = validate_package({"entry": {k: "x" for k in ["id", "model", "creator", "detector", "fusion_mode",
                                                        "controller_profile", "stack_version"]},
                              "runs": runs[:-1]})
        self.assertEqual(v["status"], "FAIL")
        self.assertTrue(any("unique repeats" in e for e in v["errors"]))

    def test_bootstrap_ci_brackets_point(self):
        s = summarize(make_runs(), with_ci=True)
        lo, hi = s["ci95"]["cdb_index"]
        self.assertLessEqual(lo, s["scores"]["cdb_index"])
        self.assertGreaterEqual(hi, s["scores"]["cdb_index"])


if __name__ == "__main__":
    unittest.main()
