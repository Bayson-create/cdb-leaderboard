#!/usr/bin/env python3
"""Resumable CDB bench runner for avsaw1 (Controlled Degradation Bench, cdb-score/1.1).

Drives one matrix row per run through the validated avsaw1 vEDGAR/CARLA/Autoware
stack and leaves a run directory that compute_run_metrics.py turns into
metrics/full_metrics.json, i.e. the same per-run evidence the formal 140-run
matrix was scored from.  Isolated from oracle_*: own folder, own GT container
name, no oracle substitution arm (perception reads the degraded cloud).

Registered intervention (identical to the formal matrix):
  S0      : identity pass-through
  S1..S3  : range_clip at 50 m, then seeded thinning keep 0.75/0.50/0.25
  trigger : 20 s of simulation time after autonomous engage, via --trigger-file

Matrix CSV columns: scenario,severity,repeat
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path('/home/avsaw1/Beichen/cdb_bench_20260923')
SCRIPTS = ROOT / 'scripts'
ENV_V = Path('/home/avsaw1/Beichen/oracle_avsaw1_20260918/env/vedgar.env')
START_SCRIPT = ROOT / 'start_system_cdb_bench.sh'
GT_NAME = 'oracle_gt_cdb'
MONO = 'tum_launch-mono-1'
VEDGAR = 'vedgar-vedgar_server-1'
CARLA = 'vedgar-tum_carla_singlepc-1'
NOMINAL_CLOUD = '/sensing/lidar/concatenated/pointcloud'
DEGRADED_CLOUD = '/sensing/lidar/degraded/pointcloud'
RANGE_LIMIT_M = 50.0
SEED = 20260805
TRIGGER_OFFSET_S = 20.0
# Map-frame goal of the validated Town10 route (start_system_*.sh GOAL_X/GOAL_Y);
# CARLA y is the negated map y.
GOAL_MAP = (105.80098724365234, -82.86859130859375)
SCENARIOS = ('lead-stopped', 'lead-slow', 'curved-urban-road', 'pedestrian-emerge',
             'red-light-violator', 'construction-obstacle', 'dynamic-cut-in')
# Formal slug -> scenarios.yaml key used by scenario_manager_semantic_v2.py
SCENARIO_KEY = {
    'lead-stopped': 'lead_stopped', 'lead-slow': 'lead_slow', 'curved-urban-road': 'curved_urban_road',
    'pedestrian-emerge': 'pedestrian_emerge_behind_parked_vehicle', 'red-light-violator': 'red_light_violator',
    'construction-obstacle': 'construction_obstacle', 'dynamic-cut-in': 'dynamic_cut_in',
}
CT = '/tmp/cdb'  # scratch dir inside the GT container


def now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class Runner:
    def __init__(self, out_root: Path):
        self.out_root = out_root
        self.runs_dir = out_root / 'runs'
        self.state_path = out_root / 'state.json'
        self.log_path = out_root / 'runner.log'
        self.stop_path = out_root / 'STOP'
        self.exclusions = out_root / 'exclusions.jsonl'
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # ── process helpers (same behaviour as oracle_runner_trackB.py) ──────────
    def log(self, msg: str) -> None:
        line = f'[{now()}] {msg}'
        print(line, flush=True)
        with self.log_path.open('a') as f:
            f.write(line + '\n')

    @staticmethod
    def run(cmd, *, cwd=None, env=None, check=False, log_path=None, capture=False, timeout=None):
        if capture:
            return subprocess.run(cmd, cwd=cwd, env=env, check=check, capture_output=True, text=True, timeout=timeout)
        if log_path:
            with open(log_path, 'a') as f:
                return subprocess.run(cmd, cwd=cwd, env=env, check=check, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
        return subprocess.run(cmd, cwd=cwd, env=env, check=check, timeout=timeout)

    def docker_names(self) -> set[str]:
        return set(self.run(['docker', 'ps', '--format', '{{.Names}}'], capture=True).stdout.split())

    def gt(self, script: str, *, capture=False, timeout=None):
        full = ("set +u; source /opt/ros/humble/setup.bash; source /autoware/install/setup.bash; set -u; "
                "export PYTHONPATH=/tmp/oracle_py:/oracle/scripts:${PYTHONPATH:-}; " + script)
        return self.run(['docker', 'exec', GT_NAME, 'bash', '-lc', full], capture=capture, timeout=timeout)

    def gt_bg(self, script: str) -> None:
        full = ("set +u; source /opt/ros/humble/setup.bash; source /autoware/install/setup.bash; set -u; "
                "export PYTHONPATH=/tmp/oracle_py:/oracle/scripts:${PYTHONPATH:-}; " + script)
        self.run(['docker', 'exec', '-d', GT_NAME, 'bash', '-lc', full])

    def wait_for_carla(self, timeout_s: int) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if CARLA in self.docker_names():
                probe = self.run(['docker', 'exec', VEDGAR, 'python3', '-c',
                                  'import carla; c=carla.Client("127.0.0.1",3000); c.set_timeout(3); w=c.get_world(); '
                                  'assert len(list(w.get_actors().filter("vehicle.edgar*"))) == 1'], capture=True)
                if probe.returncode == 0:
                    return True
            time.sleep(2)
        return False

    def compose(self, action: list[str]) -> None:
        self.run(['docker', 'compose', '--env-file', str(ENV_V), '-f', 'docker-compose.yml', '--profile', 'singlepc', *action],
                 cwd='/home/avsaw1/av20_ws/src/vedgar')

    def ensure_vedgar(self) -> None:
        if CARLA not in self.docker_names():
            self.log('starting vEDGAR/CARLA stack')
            self.compose(['up', '-d'])
        if self.wait_for_carla(120):
            return
        self.log('CARLA/EDGAR not ready after 120s -> forcing vEDGAR stack restart')
        self.compose(['down'])
        time.sleep(5)
        self.compose(['up', '-d'])
        if not self.wait_for_carla(300):
            raise RuntimeError('CARLA/EDGAR not ready after forced stack restart')

    def cleanup(self) -> None:
        self.run(['docker', 'rm', '-f', GT_NAME], capture=True)
        self.run(['docker', 'rm', '-f', MONO], capture=True)
        try:
            self.run(['docker', 'exec', VEDGAR, 'python3', '-c',
                      'import carla; c=carla.Client("127.0.0.1",3000); c.set_timeout(5); w=c.get_world(); '
                      '[a.destroy() for a in list(w.get_actors().filter("vehicle.*")) + list(w.get_actors().filter("walker.*")) '
                      'if "edgar" not in a.type_id]'], capture=True, timeout=30)
        except Exception:
            pass

    # ── one run ──────────────────────────────────────────────────────────────
    def sim_time(self) -> float | None:
        r = self.gt("timeout 8 ros2 topic echo /clock --once 2>/dev/null | awk '/sec:/ {print $2}' | head -2", capture=True, timeout=20)
        parts = [p for p in r.stdout.split() if p.strip()]
        if len(parts) >= 2:
            return int(parts[0]) + int(parts[1]) * 1e-9
        return None

    def start_degradation_node(self, severity: str) -> None:
        mode = 'identity' if severity == 'S0' else 'range_clip'
        self.gt_bg(
            f"mkdir -p {CT}; rm -f {CT}/content_*; "
            f"exec python3 /oracle/scripts/pointcloud_content_degradation.py --input-topic {NOMINAL_CLOUD} "
            f"--output-topic {DEGRADED_CLOUD} --mode {mode} --severity {severity} --range-limit {RANGE_LIMIT_M} "
            f"--seed {SEED} --trigger-sim-time 1000000000000 --trigger-file {CT}/content_degradation_trigger.json "
            f"--timing-out {CT}/content_degradation_timing.json --stats-out {CT}/content_degradation_summary.json "
            f"> {CT}/content_degradation.log 2>&1")
        time.sleep(3)

    def start_recorders(self, run_id: str, scenario_json: dict) -> None:
        ego = scenario_json.get('ego_actor_id')
        goal = {'x': GOAL_MAP[0], 'y': GOAL_MAP[1], 'carla_x': GOAL_MAP[0], 'carla_y': -GOAL_MAP[1]}
        self.gt(f"mkdir -p {CT}; echo '{json.dumps(goal)}' > {CT}/selected_goal.json")
        ego_arg = f'--ego-actor-id {ego}' if ego is not None else ''
        self.gt_bg(
            f"exec python3 /oracle/scripts/record_carla_dynamics.py --carla-ip 127.0.0.1 --carla-port 3000 --label {run_id} "
            f"--out {CT}/carla_dynamics.json --csv-out {CT}/carla_ground_truth.csv --jsonl-out {CT}/carla_ground_truth.jsonl "
            f"--events-out {CT}/carla_events.jsonl --scenario-resolved /tmp/scenario_oracle.json "
            f"--goal-file {CT}/selected_goal.json {ego_arg} --max-sec 300 --stall-hold 8 --stop-on-low-speed "
            f"> {CT}/carla_dynamics.log 2>&1")
        self.gt_bg(
            f"exec python3 /oracle/scripts/record_localization_safety.py --out {CT}/loc_safety_{run_id}.csv "
            f"--summary-out {CT}/loc_safety_{run_id}.json --max-sec 300 > {CT}/loc_safety.log 2>&1")
        time.sleep(2)

    def wait_carla_recorder(self, timeout_s: int = 330) -> str:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            r = self.gt("pgrep -f '[r]ecord_carla_dynamics.py' >/dev/null && echo alive || echo done", capture=True, timeout=20)
            if 'done' in r.stdout:
                return 'recorder_finished'
            time.sleep(5)
        return 'timeout'

    def stop_and_copy(self, run_dir: Path, run_id: str) -> None:
        self.gt("pkill -INT -f '[r]ecord_carla_dynamics.py' || true; pkill -INT -f '[r]ecord_localization_safety.py' || true")
        time.sleep(6)
        self.gt("pkill -INT -f '[p]ointcloud_content_degradation.py' || true")
        time.sleep(4)
        m = run_dir / 'metrics'
        m.mkdir(parents=True, exist_ok=True)
        (run_dir / 'config').mkdir(exist_ok=True)
        (run_dir / 'logs').mkdir(exist_ok=True)
        for name in ('carla_dynamics.json', 'carla_ground_truth.csv', 'carla_ground_truth.jsonl', 'carla_events.jsonl',
                     f'loc_safety_{run_id}.csv', f'loc_safety_{run_id}.json', 'content_degradation_summary.json',
                     'content_degradation_timing.json', 'content_degradation_trigger.json'):
            self.run(['docker', 'cp', f'{GT_NAME}:{CT}/{name}', str(m / name)], capture=True)
        for name in ('carla_dynamics.log', 'loc_safety.log', 'content_degradation.log'):
            self.run(['docker', 'cp', f'{GT_NAME}:{CT}/{name}', str(run_dir / 'logs' / name)], capture=True)
        self.run(['docker', 'cp', f'{GT_NAME}:/tmp/scenario_oracle.json', str(run_dir / 'config' / 'interaction_scenario.json')], capture=True)
        self.run(['docker', 'cp', f'{GT_NAME}:{CT}/selected_goal.json', str(run_dir / 'config' / 'selected_goal.json')], capture=True)

    @staticmethod
    def load(p: Path):
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def write_manifest(self, run_dir: Path, row: dict, run_id: str, status: str, t: dict, notes: list[str]) -> None:
        timing = self.load(run_dir / 'metrics' / 'content_degradation_timing.json') or {}
        summary = self.load(run_dir / 'metrics' / 'content_degradation_summary.json') or {}
        applied_at = timing.get('first_active_sim_time_s') or summary.get('first_active_sim_time_s')
        manifest = {
            'acquisition_schema_version': 'cdb-bench-acquisition/1.0',
            'run_id': run_id,
            'cdb_slot_id': f"{row['scenario']}__{row['severity']}__r{int(row['repeat']):02d}",
            'status': status,
            'scenario': f"Town10HD_fixed_route_{row['scenario']}",
            'replicate': int(row['repeat']),
            'host': 'avsaw1',
            'bench_root': str(ROOT),
            'ros': {'domain_id': 7, 'rmw': 'rmw_cyclonedds_cpp'},
            'goal': {'x': GOAL_MAP[0], 'y': GOAL_MAP[1]},
            'degradation': {
                'sensor': 'lidar', 'parameter': 'content', 'severity': row['severity'],
                'requested_parameter': 'none', 'requested_severity': 'S0',
                'trigger_offset_sim_time_s': TRIGGER_OFFSET_S,
                'trigger_target_sim_time_s': t.get('trigger'),
                'requested_sim_time_s': t.get('requested'),
                'applied': applied_at is not None,
                'applied_sim_time_s': applied_at,
                'confirmed_sim_time_s': applied_at,
            },
            'content_degradation': {
                'mode': 'identity' if row['severity'] == 'S0' else 'range_clip',
                'severity': row['severity'], 'range_limit_m': RANGE_LIMIT_M, 'seed': SEED,
                'perception_input_topic': DEGRADED_CLOUD,
            },
            'timestamps_utc': {'start': t.get('start_utc'), 'end': now()},
            'notes': notes,
        }
        (run_dir / 'manifest.yaml').write_text(yaml.safe_dump(manifest, sort_keys=False))

    def gate(self, run_dir: Path, row: dict) -> tuple[bool, list[str]]:
        errors: list[str] = []
        m = run_dir / 'metrics'
        fm = self.load(m / 'full_metrics.json')
        if not fm:
            return False, ['full_metrics.json missing']
        rows = (m / 'carla_ground_truth.csv').read_text().count('\n') if (m / 'carla_ground_truth.csv').exists() else 0
        if rows < 200:
            errors.append(f'carla_ground_truth.csv too short ({rows} rows)')
        if not list(m.glob('loc_safety_*.csv')):
            errors.append('loc_safety csv missing')
        deg = fm.get('degradation') or {}
        if not deg.get('applied'):
            errors.append('degradation not confirmed applied (no post-trigger frame)')
        summary = self.load(m / 'content_degradation_summary.json') or {}
        ratio = summary.get('output_input_ratio')
        if row['severity'] != 'S0' and (ratio is None or ratio >= 0.999):
            errors.append(f'no measured point reduction at {row["severity"]} (ratio={ratio})')
        tc = fm.get('task_completion') or {}
        if tc.get('completion_time_s') in (None, 0):
            errors.append('completion_time_s missing')
        return (not errors), errors

    def run_one(self, row: dict, args) -> tuple[bool, str, str]:
        stamp = int(time.time())
        run_id = f"cdbbench_{row['scenario'].replace('-', '_')}_{row['severity']}_r{int(row['repeat']):02d}_{stamp}"
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True)
        t = {'start_utc': now()}
        notes: list[str] = []
        self.log(f"START {run_id}")
        try:
            self.cleanup()
            self.ensure_vedgar()
            env = os.environ.copy()
            env.update({'ORACLE_OVERLAY': str(ROOT), 'ORACLE_CONTAINER_NAME': GT_NAME})
            self.run(['bash', str(SCRIPTS / 'start_oracle_gt_trackB.sh')], env=env, check=True,
                     log_path=str(run_dir / 'gt_start.log'))
            time.sleep(8)
            self.start_degradation_node(row['severity'])
            override = run_dir / 'mono_override.yml'
            self.run([sys.executable, str(SCRIPTS / 'oracle_make_mono_override_trackB.py'), '--arm', 'none',
                      '--out', str(override), '--overlay', str(ROOT), '--perception-input-topic', DEGRADED_CLOUD], check=True)
            env.update({
                'DISPLAY': ':0', 'XDG_RUNTIME_DIR': '/run/user/1000', 'XAUTHORITY': '/run/user/1000/gdm/Xauthority',
                'START_ORACLE_GT': 'false', 'ORACLE_ARM': 'none', 'ORACLE_PERCEPTION_INPUT_TOPIC': DEGRADED_CLOUD,
                'ORACLE_SCENARIO': SCENARIO_KEY[row['scenario']], 'ORACLE_OVERRIDE': str(override),
                'ORACLE_SEMANTIC_TRACKER_OVERLAY': '',
            })
            with open(run_dir / 'start.log', 'w') as out:
                rc = subprocess.run(['bash', str(START_SCRIPT), '--skip-vedgar', '--no-bag'], env=env,
                                    stdout=out, stderr=subprocess.STDOUT, timeout=1500).returncode
            if rc != 0:
                raise RuntimeError(f'start script rc={rc}')
            scen = self.run(['docker', 'exec', GT_NAME, 'cat', '/tmp/scenario_oracle.json'], capture=True).stdout
            scenario_json = json.loads(scen) if scen.strip() else {}
            self.start_recorders(run_id, scenario_json)
            engage = self.sim_time()
            if engage is None:
                raise RuntimeError('could not read /clock after engage')
            t['engage'] = engage
            trigger = engage + TRIGGER_OFFSET_S
            while True:
                cur = self.sim_time()
                if cur is not None and cur >= trigger - 1.0:
                    break
                time.sleep(1)
            t['requested'] = self.sim_time()
            t['trigger'] = trigger
            self.gt(f"echo '{json.dumps({'trigger_sim_time_s': trigger})}' > {CT}/content_degradation_trigger.json")
            end_reason = self.wait_carla_recorder()
            notes.append(f'run ended: {end_reason}')
            self.stop_and_copy(run_dir, run_id)
            self.write_manifest(run_dir, row, run_id, 'COMPLETE', t, notes)
            self.run([sys.executable, str(SCRIPTS / 'compute_run_metrics.py'), '--run-dir', str(run_dir)],
                     log_path=str(run_dir / 'logs' / 'compute_run_metrics.log'))
            ok, errors = self.gate(run_dir, row)
            if not ok:
                self.write_manifest(run_dir, row, run_id, 'REJECTED', t, notes + errors)
            (run_dir / 'quality.json').write_text(json.dumps({'accepted': ok, 'errors': errors}, indent=2) + '\n')
            note = 'accepted' if ok else 'rejected: ' + '; '.join(errors)
            self.log(f'DONE {run_id} {note}')
            return ok, run_id, note
        except Exception as exc:  # noqa: BLE001
            note = f'EXCEPTION {type(exc).__name__}: {exc}'
            self.log(f'FAIL {run_id} {note}')
            return False, run_id, note
        finally:
            try:
                self.cleanup()
            except Exception:
                pass

    def main(self, args) -> int:
        rows = list(csv.DictReader(open(args.matrix)))
        for r in rows:
            if r['scenario'] not in SCENARIOS or r['severity'] not in ('S0', 'S1', 'S2', 'S3'):
                raise SystemExit(f'bad matrix row: {r}')
        state = self.load(self.state_path) or {'started': now(), 'slots': {}}
        for r in rows:
            slot = f"{r['scenario']}__{r['severity']}__r{int(r['repeat']):02d}"
            state['slots'].setdefault(slot, {'status': 'pending', 'attempts': 0, 'accepted_run': None})
        self.state_path.write_text(json.dumps(state, indent=1))
        self.log(f'runner start rows={len(rows)} execute={args.execute}')
        for r in rows:
            slot = f"{r['scenario']}__{r['severity']}__r{int(r['repeat']):02d}"
            s = state['slots'][slot]
            while s['status'] != 'done' and s['attempts'] < args.max_attempts:
                if self.stop_path.exists():
                    self.log('STOP flag present -> exiting')
                    return 0
                if not args.execute:
                    self.log(f'DRY-RUN {slot}')
                    break
                s['attempts'] += 1
                self.state_path.write_text(json.dumps(state, indent=1))
                ok, run_id, note = self.run_one(r, args)
                if ok:
                    s.update(status='done', accepted_run=run_id)
                else:
                    s['status'] = 'failed'
                    with self.exclusions.open('a') as f:
                        f.write(json.dumps({'slot': slot, 'run_id': run_id, 'reason': note, 'at': now()}) + '\n')
                self.state_path.write_text(json.dumps(state, indent=1))
        self.log('runner finished')
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--matrix', required=True, help='CSV with scenario,severity,repeat')
    ap.add_argument('--out', default=str(ROOT / 'results' / 'runs_root'), help='results root (runs/, state.json, exclusions.jsonl)')
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--max-attempts', type=int, default=2)
    args = ap.parse_args()
    return Runner(Path(args.out)).main(args)


if __name__ == '__main__':
    raise SystemExit(main())
