"""Turn a "Submit results" GitHub issue into a submission (used by .github/workflows/submission.yml).

    python -m cdb_score.intake --issue-body body.md --issue-url URL --submitted-at ISO --workdir DIR

Everything in the issue is untrusted input: the package link must be https, the download is capped,
the file must parse as JSON, and nothing from it is executed. Writes into DIR:
    result.json   machine-readable outcome (status, entry id, errors)
    comment.md    the reply posted on the issue
    package.json  the downloaded package (only when it parsed)
    registry_entry.json  the registry row to add (only when status is PASS or PARTIAL)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from . import spec
from .score import summarize
from .validate import ENTRY_ID_RE, FUSION_MODES, validate_package

MAX_BYTES = 20 * 1024 * 1024
FIELDS = {
    "Entry id": "id",
    "Model name": "model",
    "Creator / organisation": "creator",
    "Detector": "detector",
    "Fusion mode": "fusion_mode",
    "Controller profile": "controller_profile",
    "Stack version": "stack_version",
    "Commit": "commit",
    "Package URL": "package_url",
    "Notes": "notes",
}


def parse_issue_form(body: str) -> dict[str, str]:
    """GitHub issue forms render each field as '### <label>' followed by the answer."""
    out: dict[str, str] = {}
    for block in re.split(r"^### ", body or "", flags=re.M)[1:]:
        label, _, value = block.partition("\n")
        key = FIELDS.get(label.strip())
        if not key:
            continue
        value = value.strip()
        if value in ("_No response_", ""):
            continue
        out[key] = value
    return out


def download(url: str) -> bytes:
    u = urlparse(url)
    if u.scheme != "https" or not u.netloc:
        raise ValueError("package URL must be an https:// link")
    req = urllib.request.Request(url, headers={"User-Agent": "cdb-leaderboard-intake"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - scheme checked above
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"package is larger than {MAX_BYTES // (1024 * 1024)} MB")
    return data


def _fmt(x) -> str:
    return "–" if x is None else f"{x:.2f}"


def process(form: dict[str, str], fetch=download) -> tuple[dict, str, dict | None, dict | None]:
    """Returns (result, comment_markdown, package or None, registry_entry or None)."""
    problems: list[str] = []
    eid = form.get("id", "")
    if not ENTRY_ID_RE.match(eid):
        problems.append("**Entry id** must be 3–80 characters of lowercase letters, digits, `.`, `_` or `-`.")
    if form.get("fusion_mode") and form["fusion_mode"] not in FUSION_MODES:
        problems.append(f"**Fusion mode** must be one of {', '.join(sorted(FUSION_MODES))}.")
    for label, key in FIELDS.items():
        if key in ("commit", "notes"):
            continue
        if not form.get(key):
            problems.append(f"**{label}** is empty.")
    pkg = None
    if not problems:
        try:
            pkg = json.loads(fetch(form["package_url"]).decode("utf-8"))
            if not isinstance(pkg, dict):
                raise ValueError("the file is JSON but not an object")
        except Exception as exc:  # noqa: BLE001 - every failure becomes a readable issue comment
            problems.append(f"Could not read the package: {exc}")
    if problems:
        result = {"status": "FAIL", "entry_id": eid, "errors": problems}
        comment = ("### ❌ Submission not accepted yet\n\n" + "\n".join(f"- {p}" for p in problems) +
                   "\n\nEdit the issue to fix this; the check runs again automatically.")
        return result, comment, pkg, None

    # the form is the source of truth for the entry block; it must agree with the package
    entry = {k: form[k] for k in ("id", "model", "creator", "detector", "fusion_mode", "controller_profile", "stack_version")}
    if form.get("commit"):
        entry["commit"] = form["commit"]
    pkg_entry = pkg.get("entry") or {}
    if pkg_entry.get("id") and pkg_entry["id"] != entry["id"]:
        problems.append(f"The package says entry id `{pkg_entry['id']}` but the form says `{entry['id']}`.")
    pkg["entry"] = {**pkg_entry, **entry}

    v = validate_package(pkg)
    errors = problems + v["errors"]
    status = "FAIL" if errors else v["status"]
    lines = [f"**Entry:** `{entry['id']}` · {entry['model']}",
             f"**Runs:** {v['n_runs']} · **complete cells:** {v['n_cells_complete']}/{v['n_cells_total']} "
             f"· **warnings:** {len(v['warnings'])}", ""]
    if status == "FAIL":
        head = "### ❌ Submission failed validation"
        lines += ["Fix these and edit the issue (or update the file behind the link):", ""]
        lines += [f"- {e}" for e in errors[:30]]
        if len(errors) > 30:
            lines.append(f"- … and {len(errors) - 30} more")
        registry_entry = None
    else:
        s = summarize(pkg["runs"], with_ci=(status == "PASS"))
        if status == "PASS":
            head = "### ✅ Submission passed and was scored"
            sc, ci = s["scores"], s["ci95"] or {}

            def row(name, key):
                c = ci.get(key)
                return f"| {name} | {_fmt(sc[key])} | " + (f"[{_fmt(c[0])}, {_fmt(c[1])}]" if c else "–") + " |"
            lines += ["| Score | Value | 95 % interval |", "|---|---:|---|",
                      row("CDB index", "cdb_index"), row("Safety", "safety"),
                      row("Comfort & handling", "comfort"), row("Operation", "operation")]
        else:
            head = "### 🟡 Submission accepted as incomplete (not ranked)"
            lines += ["The package is well formed but does not cover all 28 cells "
                      f"(7 scenarios × S0–S3 × {spec.REPEATS_REQUIRED} repeats). It will be listed as "
                      "*incomplete* with per-scenario scores only and no CDB total.", ""]
        per = [(k, x["axes"]) for k, x in s["per_scenario"].items() if any(a is not None for a in x["axes"].values())]
        if per:
            lines += ["", "| Scenario | Safety | Comfort & handling | Operation |", "|---|---:|---:|---:|"]
            lines += [f"| {spec.SCENARIO_BY_SLUG[k].display} | {_fmt(a['safety'])} | {_fmt(a['comfort'])} | {_fmt(a['operation'])} |"
                      for k, a in per]
        if v["coverage"]:
            lines += ["", f"<details><summary>{len(v['coverage'])} cells missing or short</summary>", ""]
            lines += [f"- {c}" for c in v["coverage"]] + ["", "</details>"]
        lines += ["", "A pull request adding this entry has been opened. The leaderboard updates when it is merged."]
        registry_entry = {**entry, "status": "real", "color": "#6b7280",
                          "notes": form.get("notes") or "Submitted through the Submit results form."}
    result = {"status": status, "entry_id": entry["id"], "errors": errors[:50],
              "n_runs": v["n_runs"], "cells_complete": f"{v['n_cells_complete']}/{v['n_cells_total']}"}
    return result, head + "\n\n" + "\n".join(lines), pkg, registry_entry


def write_registry(registry_path: Path, entries: list[dict]) -> None:
    """One entry per line, the format the repository keeps under version control."""
    body = ",\n".join("  " + json.dumps(e, ensure_ascii=False) for e in entries)
    registry_path.write_text('{\n "entries": [\n' + body + "\n ]\n}\n", encoding="utf-8")


def register(registry_path: Path, new_entry: dict) -> str:
    """Add the entry, or replace a pending/real entry with the same id. Returns 'added' or 'replaced'."""
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    entries = data["entries"]
    for i, e in enumerate(entries):
        if e["id"] == new_entry["id"]:
            entries[i] = {**e, **new_entry}
            write_registry(registry_path, entries)
            return "replaced"
    entries.append(new_entry)
    write_registry(registry_path, entries)
    return "added"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["register"]:
        rp = argparse.ArgumentParser(prog="cdb_score.intake register")
        rp.add_argument("--registry", required=True)
        rp.add_argument("--entry", required=True)
        r = rp.parse_args(argv[1:])
        print(register(Path(r.registry), json.loads(Path(r.entry).read_text(encoding="utf-8"))))
        return 0
    ap = argparse.ArgumentParser(prog="cdb_score.intake")
    ap.add_argument("--issue-body", required=True)
    ap.add_argument("--issue-url", required=True)
    ap.add_argument("--submitted-at", required=True)
    ap.add_argument("--workdir", required=True)
    a = ap.parse_args(argv)
    work = Path(a.workdir)
    work.mkdir(parents=True, exist_ok=True)
    form = parse_issue_form(Path(a.issue_body).read_text(encoding="utf-8"))
    result, comment, pkg, reg = process(form)
    if pkg is not None:
        (work / "package.json").write_text(json.dumps(pkg, indent=1), encoding="utf-8")
    if reg is not None:
        reg = {**reg, "submitted_at": a.submitted_at, "submission_url": a.issue_url}
        (work / "registry_entry.json").write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    (work / "result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    (work / "comment.md").write_text(comment + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
