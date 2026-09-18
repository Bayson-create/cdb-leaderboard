# Controlled Degradation Bench — Leaderboard

Static leaderboard + scoring CLI for the **Controlled Degradation Bench (CDB)**: closed-loop evaluation of automated-driving software stacks under a registered LiDAR degradation (CARLA 0.9.16 · vEDGAR 1.0.3 · Autoware). Every entry is driven through the same 7 scenarios × S0–S3 × 5 accepted repeats (140 runs) and scored on three **degradation-robustness indices** — Safety, Comfort & handling, Operation — plus their equal-weight mean, the CDB Index.

Design language after [artificialanalysis.ai](https://artificialanalysis.ai). No build step for the site; no third-party Python dependencies.

```
cdb_score/      scoring package: spec.py (metric registry) · validate.py · score.py · build.py · cli.py
submissions/    one directory per entry, each with package.json (run-level metrics, 140 runs)
registry/       models.json — leaderboard entries (real or pending) and the "Updates" feed
docs/           static site (served as GitHub Pages): index.html · model.html · methodology.html · submit.html · assets/ · data/
tests/          unittest suite for the scorer
```

## Quick start

```bash
python3 -m unittest tests/test_score.py                       # scorer tests
python3 -m cdb_score import-formal28                          # (maintainer) rebuild the reference package from the evidence snapshot
python3 -m cdb_score validate submissions/autoware-0.3.8_bevfusion-lidar_baseline
python3 -m cdb_score score    submissions/autoware-0.3.8_bevfusion-lidar_baseline
python3 -m cdb_score build                                    # -> docs/data/leaderboard.json + leaderboard.js
cd docs && python3 -m http.server 8790                        # http://127.0.0.1:8790
```

## Score (spec `cdb-score/1.1`)

For scenario *q*, metric *m* on axis *a*, severity *s* ∈ {S1,S2,S3}: `b = mean_S0(m)`, `d_s = mean_Ss(m)`, `w_s = max(0, sign·(d_s − b))`, `r_qms = 1 − clip(w_s / scale_m(b), 0, 1)`, `r_qm = (r_S1 + r_S2 + 2·r_S3)/4`, `Axis_a = 100·mean(r_qm)`, `CDB = mean(Safety, Comfort & handling, Operation)`. Improvement is not rewarded; undefined cells (TTC without an actor, route completion for the controlled stop) are skipped, never imputed; every entry is normalised to its own S0. Intervals: 95 % percentile bootstrap over runs resampled within each scenario × severity cell (2 000 draws, seed 20260907). Full text: `docs/methodology.html`.

Metric registry and scales: `cdb_score/spec.py` (Safety 4 metrics; Comfort & handling 9, including MRM / hard / severe braking; Operation 4).

## Reference entry

`autoware-0.3.8_bevfusion-lidar_baseline` — built from `formal28_pattern_extraction_20260915/input_snapshot/remote_derived_evidence/<run>/metrics/full_metrics.json` for the 140 runs in `formal_140_run_selection.json` (7,418/7,418 derived files hash-verified). v1.1 reference scores: **CDB 88.93** (95 % CI 84.53–91.75), **Safety 89.67** (83.09–95.75), **Comfort & handling 78.76** (70.73–84.44), **Operation 98.35** (95.62–98.65). Pending rows in `registry/models.json` are configurations available in the reference stack that have **not** been run; they carry no numbers.

## Mechanism Robustness (`docs/mechanisms.html`)

A second, complementary view: the same reference stack scored on **every** registered degradation mechanism (not just range-clip), grouped into four axes — spatial coverage, point-cloud density (continuous thinning), temporal sampling (rate / message delay), object-level recognition loss — each on its own native severity ladder. Includes pooled vs cohort-comparability-controlled robustness scores, Wilson-CI breaking points, a recall/NDT-dropout mediation analysis, and a benchmark comparison against KITTI-C/nuScenes-C/Robo3D/MultiCorrupt. Built by `cdb_score/build_mechanism_robustness.py` from the `robustness_analysis_20260916` package (`ma-xie_vedgar_perception_degradation_main_analysis/results/robustness_analysis_20260916/`) into `docs/data/mechanism_robustness.json`; re-run that script and `git add`/commit to refresh it after the source package changes. No mechanism-axis score is comparable in magnitude to the CDB Index above, or to another axis — see the callout on the page itself.

## Submitting an entry

See `SUBMISSION.md` (also rendered at `docs/submit.html`).

## Deploy

Live site: **https://bayson-create.github.io/cdb-leaderboard/** (GitHub Pages, branch `main`, folder `/docs`). To deploy elsewhere, copy `docs/` to any static host — the pages read `data/leaderboard.js`, so they also work from `file://`.

## Boundaries

Indices are relative to each configuration's own S0 in one simulator, map, route, vehicle model and controller profile; they are not absolute performance and not a road safety rate. Stage records locate where a change was first measured, not which component caused it. Severity labels are comparable only inside one intervention family.
