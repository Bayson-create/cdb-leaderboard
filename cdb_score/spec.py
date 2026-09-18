"""Metric, scenario and severity registry for the Controlled Degradation Bench (CDB) score.

Everything the score depends on is declared here so that the methodology page,
the validator and the scorer read one source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

SPEC_VERSION = "cdb-score/1.1"

SEVERITIES = ["S0", "S1", "S2", "S3"]
SEVERITY_WEIGHTS = {"S1": 1.0, "S2": 1.0, "S3": 2.0}  # S3 counts double
REPEATS_REQUIRED = 5

# Registered intervention (formal_supplementary_v1): planar clip at 50 m, then seeded thinning.
INTERVENTION = {
    "mechanism": "content_range_clip",
    "range_limit_m": 50.0,
    "keep_probability": {"S0": 1.0, "S1": 0.75, "S2": 0.50, "S3": 0.25},
    "mode": {"S0": "identity", "S1": "range_clip", "S2": "range_clip", "S3": "range_clip"},
}

@dataclass(frozen=True)
class Scenario:
    slug: str
    display: str
    cohort: str
    pipeline_slug: str
    description: str

SCENARIOS: list[Scenario] = [
    Scenario("lead-stopped", "Stopped lead", "supplementary_extension", "lead-stopped",
             "Fully stopped lead vehicle in the ego lane, 25 m ahead"),
    Scenario("lead-slow", "Slow lead", "supplementary_formal_60", "lead-slow",
             "Lead vehicle 30 m ahead driving at 2.5 m/s"),
    Scenario("curved-urban-road", "Urban curve", "supplementary_extension", "curved-urban-road",
             "Urban curve with no other moving actor"),
    Scenario("pedestrian-emerge", "Occluded pedestrian", "supplementary_formal_60",
             "pedestrian-emerge-behind-parked-vehicle",
             "Pedestrian crossing from behind a parked vehicle"),
    Scenario("red-light-violator", "Cross traffic", "supplementary_formal_60", "red-light-violator",
             "Cross-traffic vehicle entering the junction at 7 m/s"),
    Scenario("construction-obstacle", "Construction", "supplementary_extension", "construction-obstacle",
             "Static construction obstacle blocking the lane"),
    Scenario("dynamic-cut-in", "Dynamic cut-in", "supplementary_extension", "dynamic-cut-in",
             "Vehicle cutting in from the adjacent lane at 4 m/s"),
]
SCENARIO_BY_SLUG = {s.slug: s for s in SCENARIOS}
PIPELINE_TO_SLUG = {s.pipeline_slug: s.slug for s in SCENARIOS}

AXES = ["safety", "comfort", "operation"]
AXIS_DISPLAY = {"safety": "Safety", "comfort": "Comfort & handling", "operation": "Operation"}

@dataclass(frozen=True)
class Metric:
    key: str                 # dotted path inside full_metrics.json
    axis: str
    display: str
    unit: str
    better_when: str         # "lower" | "higher"
    scale: Callable[[float], float]  # baseline value -> normalising scale
    scale_text: str          # human-readable description of the scale
    transform: Optional[Callable[[float], float]] = None  # e.g. abs()
    requires: Optional[str] = None   # dotted key that must be truthy for the metric to apply
    absolute_column: bool = False    # also shown as an "absolute at S3" column
    optional: bool = False           # may be null for scenario types where it is undefined

def _rel(frac: float, floor: float = 0.0) -> Callable[[float], float]:
    return lambda b: max(frac * abs(b), floor)

METRICS: list[Metric] = [
    # ---- Safety (vehicle-outcome safety endpoints; handling moved to Comfort & handling in v1.1) ----
    Metric("safety.collision_count", "safety", "Collisions", "count", "lower",
           lambda b: 1.0, "1 collision", absolute_column=True),
    Metric("safety.min_ttc_s", "safety", "Minimum TTC", "s", "higher",
           _rel(1.0, 0.5), "S0 value (min 0.5 s)", requires="safety.ttc_applicable", absolute_column=True),
    Metric("event_rates.ttc_warning.per_km", "safety", "TTC warnings", "/km", "lower",
           _rel(1.0, 8.0), "S0 value (min 8 /km)", requires="safety.ttc_applicable"),
    Metric("safety.lane_invasion_count", "safety", "Lane invasions", "count", "lower",
           _rel(1.0, 2.0), "S0 value (min 2)"),
    Metric("safety.mrm_event_count", "comfort", "MRM events", "count", "lower",
           _rel(1.0, 2.0), "S0 value (min 2)"),
    Metric("event_rates.hard_braking.per_km", "comfort", "Hard braking", "/km", "lower",
           _rel(1.0, 40.0), "S0 value (min 40 /km)"),
    Metric("event_rates.severe_braking.per_km", "comfort", "Severe braking", "/km", "lower",
           _rel(1.0, 20.0), "S0 value (min 20 /km)"),
    # ---- Comfort & handling (handling folded in, matching thesis §3.6.2) ----
    Metric("comfort.max_longitudinal_jerk_mps3", "comfort", "Max longitudinal jerk", "m/s³", "lower",
           _rel(1.0, 20.0), "S0 value (min 20 m/s³)"),
    Metric("comfort.max_jerk_vector_magnitude_mps3", "comfort", "Max jerk magnitude", "m/s³", "lower",
           _rel(1.0, 20.0), "S0 value (min 20 m/s³)"),
    Metric("comfort.max_yaw_acceleration_radps2", "comfort", "Max yaw acceleration", "rad/s²", "lower",
           _rel(1.0, 0.1), "S0 value (min 0.1 rad/s²)"),
    Metric("comfort.max_yaw_rate_radps", "comfort", "Max yaw rate", "rad/s", "lower",
           _rel(1.0, 0.05), "S0 value (min 0.05 rad/s)"),
    Metric("comfort.max_lateral_acceleration_mps2", "comfort", "Max lateral acceleration", "m/s²", "lower",
           _rel(1.0, 0.5), "S0 value (min 0.5 m/s²)"),
    Metric("comfort.min_longitudinal_acceleration_mps2", "comfort", "Peak deceleration", "m/s²", "lower",
           _rel(1.0, 1.0), "|S0| value (min 1 m/s²)", transform=abs),
    # ---- Operation ----
    Metric("task_completion.completion_time_s", "operation", "Completion time", "s", "lower",
           _rel(0.25, 5.0), "25 % of S0 completion time", absolute_column=True),
    Metric("task_completion.no_progress_duration_s", "operation", "No-progress duration", "s", "lower",
           _rel(1.0, 5.0), "S0 value (min 5 s)"),
    Metric("task_completion.blocked_events", "operation", "Blocked events", "count", "lower",
           lambda b: 1.0, "1 event"),
    Metric("task_completion.route_completion", "operation", "Route completion", "fraction", "higher",
           _rel(1.0, 0.1), "S0 value (min 0.1)", absolute_column=True, optional=True),
]
METRIC_BY_KEY = {m.key: m for m in METRICS}
METRICS_BY_AXIS = {a: [m for m in METRICS if m.axis == a] for a in AXES}

# Keys copied verbatim from full_metrics.json into a submission package (subset that the score
# and the absolute columns need, plus provenance of the intervention timing).
PACKAGE_METRIC_KEYS = [m.key for m in METRICS] + [
    "safety.ttc_applicable", "safety.hard_braking_count", "safety.severe_braking_count",
    "route_metrics.travel_time_s", "task_completion.success", "distance_km",
    "degradation.severity", "degradation.applied", "degradation.applied_sim_time_s",
    "degradation.requested_sim_time_s", "degradation.confirmed_sim_time_s",
    "localization.ndt_score_min", "route_metrics.localization_error_m",
]

BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20260907

def letter(score: float) -> str:
    """Confidence-style letter used for the CI width, mirroring the thesis A–D convention."""
    if score >= 90:
        return "A"
    if score >= 70:
        return "B"
    if score >= 40:
        return "C"
    return "D"
