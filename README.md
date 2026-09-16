# Controlled Degradation Bench — Leaderboard

Static leaderboard + scoring CLI for the **Controlled Degradation Bench (CDB)**: closed-loop evaluation of automated-driving software stacks under a registered LiDAR degradation (CARLA 0.9.16 · vEDGAR 1.0.3 · Autoware). Every entry is driven through the same 7 scenarios × S0–S3 × 5 accepted repeats (140 runs) and scored on three **degradation-robustness indices** — Safety, Comfort, Operation — plus their equal-weight mean, the CDB Index.

Design language after [artificialanalysis.ai](https://artificialanalysis.ai). No build step for the site; no third-party Python dependencies.

```
cdb_score/      scoring package: spec.py (metric registry) · validate.py · score.py · build.py · cli.py
submissions/    one directory per entry, each with package.json (run-level metrics, 140 runs)
registry/       models.json — leaderboard entries (real or pending) and the "Updates" feed
site/           static site: index.html · model.html · methodology.html · submit.html · assets/ · data/
tests/          unittest suite for the scorer
```

## Quick start

```bash
python3 -m unittest tests/test_score.py                       # scorer tests
python3 -m cdb_score import-formal28                          # (maintainer) rebuild the reference package from the evidence snapshot
python3 -m cdb_score validate submissions/autoware-0.3.8_bevfusion-lidar_baseline
python3 -m cdb_score score    submissions/autoware-0.3.8_bevfusion-lidar_baseline
python3 -m cdb_score build                                    # -> site/data/leaderboard.json + leaderboard.js
cd site && python3 -m http.server 8790                        # http://127.0.0.1:8790
```

## Score (spec `cdb-score/1.0`)

For scenario *q*, metric *m* on axis *a*, severity *s* ∈ {S1,S2,S3}: `b = mean_S0(m)`, `d_s = mean_Ss(m)`, `w_s = max(0, sign·(d_s − b))`, `r_qms = 1 − clip(w_s / scale_m(b), 0, 1)`, `r_qm = (r_S1 + r_S2 + 2·r_S3)/4`, `Axis_a = 100·mean(r_qm)`, `CDB = mean(Safety, Comfort, Operation)`. Improvement is not rewarded; undefined cells (TTC without an actor, route completion for the controlled stop) are skipped, never imputed; every entry is normalised to its own S0. Intervals: 95 % percentile bootstrap over runs resampled within each scenario × severity cell (2 000 draws, seed 20260907). Full text: `site/methodology.html`.

Metric registry and scales: `cdb_score/spec.py` (Safety 7 metrics incl. handling, Comfort 6, Operation 4).

## Reference entry

`autoware-0.3.8_bevfusion-lidar_baseline` — built from `formal28_pattern_extraction_20260915/input_snapshot/remote_derived_evidence/<run>/metrics/full_metrics.json` for the 140 runs in `formal_140_run_selection.json` (7,418/7,418 derived files hash-verified). Pending rows in `registry/models.json` are configurations available in the reference stack that have **not** been run; they carry no numbers.

## Submitting an entry

See `SUBMISSION.md` (also rendered at `site/submit.html`).

## Deploy

GitHub Pages: publish the `site/` directory (Settings → Pages → branch `main`, folder `/site`), or copy `site/` to any static host. The pages read `data/leaderboard.js`, so they also work from `file://`.

## Boundaries

Indices are relative to each configuration's own S0 in one simulator, map, route, vehicle model and controller profile; they are not absolute performance and not a road safety rate. Stage records locate where a change was first measured, not which component caused it. Severity labels are comparable only inside one intervention family.
