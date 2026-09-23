# Bench runner

`cdb_bench_runner.py` drives the Controlled Degradation Bench on the reference machine
(vEDGAR + CARLA 0.9.16 + Autoware `tum_launch`, avsaw1 layout). One matrix row = one run:
start stack → apply the registered intervention (S0 identity; S1–S3 = 50 m range clip + seeded
thinning keep 0.75/0.50/0.25, triggered 20 s of sim time after engage via a trigger file) →
record CARLA ground truth/dynamics and localisation/MRM samples → write `manifest.yaml` →
`compute_run_metrics.py` → quality gate. Accepted runs feed `python -m cdb_score package`.

```
python3 bench/cdb_bench_runner.py --matrix matrices/full_140.csv --out results/<entry-id>            # dry run
python3 bench/cdb_bench_runner.py --matrix matrices/full_140.csv --out results/<entry-id> --execute  # ~14–16 h
```

Matrix CSV header: `scenario,severity,repeat`. Resumable (`state.json`), stoppable (touch `STOP`),
rejected attempts in `exclusions.jsonl`. Paths at the top of the file (`ROOT`, `ENV_V`, container
names) are specific to the reference machine; adapt them for another host.

Status (2026-09-23): deployed to avsaw1 under `/home/avsaw1/Beichen/cdb_bench_20260923/` and
dry-run tested; the first live pilot is pending free GPU capacity on the shared machine.
