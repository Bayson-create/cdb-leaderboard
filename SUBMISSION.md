# Submitting results to the Controlled Degradation Bench

1. **Plug in your model** in the reference pipeline (`ma-xie_vedgar_perception_degradation`):
   - shipped detector: set `lidar_detection_model` (`centerpoint`, `centerpoint_tiny`, `transfusion`, `pointpainting`, `bevfusion_lidar`, `bevfusion_camera_lidar`) and `model_path` in `config/tum_launch/env`;
   - camera–LiDAR fusion: `run_full_pipeline.sh --perception-profile camera_lidar`;
   - your own detector: subscribe to `/sensing/lidar/degraded/pointcloud` (`--perception-input-topic`) and publish `DetectedObjects` on `/perception/object_recognition/objects`; keep everything downstream at the reference build.
   Keep `--controller-profile baseline` unless the entry is a controller comparison.
2. **Run the full matrix** with `scripts/generate_supplementary_schedule.py` + `scripts/run_supplementary_campaign.py … --execute` (7 scenarios × S0–S3 × 5 accepted repeats). Rejected runs stay in `exclusions.jsonl`; slots are re-attempted, never filled by a rejected run.
3. **Package**: one `package.json` (`schema: cdb-submission/1.0`) with `entry`, 140 `runs` (`run_id, scenario, severity, repeat, metrics ⊂ full_metrics.json, source_sha256`) and `provenance`. `python -m cdb_score demo-package template.json` writes a template; `cdb_score/spec.py::PACKAGE_METRIC_KEYS` lists the keys.
4. **Validate and score locally**: `python -m cdb_score validate <dir>` must print `"status": "PASS"` with 28/28 cells; `python -m cdb_score score <dir>` prints the indices and intervals you will see on the board.
5. **Submit**: add `submissions/<entry-id>/package.json`, add the entry (`"status": "real"`) to `registry/models.json`, open a pull request. CI runs validate + build + tests and regenerates the leaderboard from run-level values. Keep raw run directories (rosbags, camera sidecars, CARLA ground truth) for one year for audit.

**Access.** The reference images (`autoware/microservice/mono:cuda-humble-x86_64-0.3.8`, `carla-ros2-release:tum_0.9.16_v2`, vEDGAR 1.0.3) and `tum_models` weights live on the TUM LRZ GitLab registry and require an account; a redistributable image set is the main open item before external submissions can be accepted at scale.
