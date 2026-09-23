# Submitting results to the Controlled Degradation Bench

**Short version:** run the bench, build `package.json`, check it locally, then open the
[submission form](https://github.com/Bayson-create/cdb-leaderboard/issues/new?template=submission.yml).
A bot validates and scores the package, replies on the issue, and opens a pull request;
merging that pull request rebuilds the leaderboard.

1. **Run the bench** (7 scenarios × S0–S3 × 5 accepted repeats = 140 runs) with
   `bench/cdb_bench_runner.py --matrix <list.csv> --out <dir> --execute` on a machine with the
   reference stack. Each accepted run folder holds `manifest.yaml` and `metrics/full_metrics.json`
   (from `compute_run_metrics.py`). Rejected attempts go to `exclusions.jsonl` and never fill a slot.
   What may differ between entries: the detector (`lidar_detection_model`, `model_path`), your own detector
   publishing `DetectedObjects` from `/sensing/lidar/degraded/pointcloud`, or, as a separate entry, the controller profile.
2. **Package:** `python -m cdb_score package --runs <dir>/runs --entry entry.json --out package.json`
   (format `cdb-submission/1.0`, see `submissions/SCHEMA.json`; per-run `source_sha256` is added automatically).
3. **Check locally:** `python -m cdb_score validate package.json` → `PASS` (28/28 cells, ranked),
   `PARTIAL` (well formed but incomplete: shown per scenario, never ranked) or `FAIL` (with reasons);
   `python -m cdb_score score package.json` prints what the bot will post. Score spec `cdb-score/1.1`.
4. **Submit** through the form with an https link to `package.json`. What the automation does:
   - `.github/workflows/submission.yml` (on the issue): download (https only, ≤ 20 MB, JSON only, never executed),
     validate, score, comment, and for PASS/PARTIAL push `submission/<id>` and open a pull request;
   - `.github/workflows/ci.yml` (on pull requests and `main`): unit tests, validate every package, byte-identical rebuild;
   - `.github/workflows/publish.yml` (after merge): tests, `cdb_score build`, commit `docs/data/leaderboard.{json,js}`, redeploy Pages.
   The home-page news and "last submission" date are derived from `submitted_at` in `registry/models.json`,
   so they change only when a submission lands.
5. Keep raw run folders (recordings, CARLA ground truth) for one year for audit.

**Access.** The reference images (`autoware/microservice/mono:cuda-humble-x86_64-0.3.8`, CARLA `tum_0.9.16`,
vEDGAR 1.0.3) and `tum_models` weights live on the TUM LRZ GitLab registry and require an account;
a redistributable image set is the main open item before outside teams can run the bench unaided.
