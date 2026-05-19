#!/usr/bin/env python3
"""Mine snapshot CI check-run results for each commit in post-adoption PRs.

For every merged PR created after snapshot adoption (Jan 2026), fetches:
  1. All commits in the PR
  2. The "Run snapshot tests" check-run result for each commit

NOTE: "Run snapshot tests" is NOT a required status check on openhands-cli.
Required checks are: unit tests, pre-commit, and binary builds. Snapshot
CI is advisory — it flags mismatches but does not block merge.

Outputs a CSV with one row per PR:
  - pr_number, pr_title, pr_type, has_snapshot_file_changes
  - commits (total count)
  - snapshot_ci_results: per-commit pass/fail sequence e.g. "fail,fail,pass"
  - first_commit_snapshot_ci: pass/fail/none
  - last_commit_snapshot_ci: pass/fail/none
  - ever_failed_snapshot_ci: true/false
  - classification (uses BOTH ever_failed AND last_commit_ci):
      "resolved_baseline" = CI failed mid-PR, last commit passed, PR has snapshot file changes
                            (developer updated baseline to acknowledge intentional change)
      "resolved_code_fix" = CI failed mid-PR, last commit passed, PR has NO snapshot file changes
                            (developer fixed code to match existing baseline)
      "merged_with_failure" = CI failed and last commit still failing when merged
                              (developer ignored advisory failure or it was flaky)
      "always_passed"     = snapshot CI never failed during the PR
      "no_ci_data"        = no snapshot check-run found on any commit

Usage:
    export GITHUB_TOKEN=...
    python scripts/mine_snapshot_ci.py
"""

import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = "OpenHands/openhands-cli"
SNAPSHOT_JOB_NAME = "Run snapshot tests"
ADOPTION_MONTH = "2026-01"
DATA_DIR = Path("data")
OUTPUT = DATA_DIR / "snapshot_ci_results_v2.csv"


def gh_api(endpoint, jq_filter=None):
    """Call GitHub API via gh CLI. Returns parsed JSON."""
    cmd = ["gh", "api", endpoint]
    if jq_filter:
        cmd += ["--jq", jq_filter]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  API error: {result.stderr.strip()}", file=sys.stderr)
        return None
    if jq_filter:
        return result.stdout.strip()
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def get_pr_commits(pr_number):
    """Get list of commit SHAs for a PR."""
    commits = []
    page = 1
    while True:
        data = gh_api(f"repos/{REPO}/pulls/{pr_number}/commits?per_page=100&page={page}")
        if not data:
            break
        commits.extend(c["sha"] for c in data)
        if len(data) < 100:
            break
        page += 1
    return commits


def get_snapshot_check_result(sha):
    """Get the snapshot test check-run conclusion for a commit SHA.
    Returns 'success', 'failure', 'cancelled', 'skipped', or None."""
    data = gh_api(f"repos/{REPO}/commits/{sha}/check-runs")
    if not data or "check_runs" not in data:
        return None

    for run in data["check_runs"]:
        if run.get("name") == SNAPSHOT_JOB_NAME:
            return run.get("conclusion")
    return None


def classify_pr(ever_failed, last_commit_ci, has_snapshot_changes):
    if not ever_failed:
        return "always_passed"
    if last_commit_ci == "failure":
        return "merged_with_failure"
    if has_snapshot_changes:
        return "resolved_baseline"
    else:
        return "resolved_code_fix"


def load_existing_results():
    """Load already-mined results for resume support."""
    if not OUTPUT.exists():
        return {}
    with open(OUTPUT) as f:
        return {r["pr_number"]: r for r in csv.DictReader(f)}


FIELDNAMES = [
    "pr_number", "pr_title", "pr_type", "has_snapshot_file_changes",
    "num_commits", "snapshot_ci_sequence", "first_commit_ci", "last_commit_ci",
    "ever_failed", "classification",
]


def save_results(results):
    with open(OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(results)


def print_summary(results):
    from collections import Counter
    print(f"\n{'='*60}")
    print(f"Results saved to {OUTPUT}")
    print(f"{'='*60}")

    counts = Counter(r["classification"] for r in results)
    total = len(results)
    for cls in ["resolved_code_fix", "resolved_baseline", "merged_with_failure",
                "always_passed", "no_ci_data"]:
        n = counts.get(cls, 0)
        pct = 100 * n / total if total else 0
        print(f"  {cls:<22s}: {n:3d} ({pct:.0f}%)")

    failed = [r for r in results if r["classification"] in
              ("resolved_code_fix", "resolved_baseline", "merged_with_failure")]
    if failed:
        print(f"\n{'='*60}")
        print(f"PRs WHERE SNAPSHOT CI FAILED ({len(failed)})")
        print(f"{'='*60}")
        for cls, label in [
            ("resolved_code_fix", "🔧 RESOLVED — code fixed (no baseline update)"),
            ("resolved_baseline", "📝 RESOLVED — baseline updated"),
            ("merged_with_failure", "⚠️  MERGED WITH FAILURE (advisory CI ignored/flaky)"),
        ]:
            group = [r for r in failed if r["classification"] == cls]
            if group:
                print(f"\n  {label} ({len(group)}):")
                for r in group:
                    print(f"    #{r['pr_number']} [{r['pr_type']}] last_ci={r['last_commit_ci']}")
                    print(f"      {r['pr_title'][:75]}")


def main():
    csv_path = DATA_DIR / "categorized_prs.csv"
    with open(csv_path) as f:
        all_prs = list(csv.DictReader(f))

    post_adoption = [
        r for r in all_prs
        if r.get("pr_created_at", "")[:7] >= ADOPTION_MONTH
    ]
    print(f"Post-adoption merged PRs: {len(post_adoption)}")

    existing = load_existing_results()
    if existing:
        print(f"Resuming — {len(existing)} PRs already mined")

    results = []
    for i, pr in enumerate(post_adoption):
        pr_num = pr["pr_number"]
        pr_title = pr["pr_title"]
        pr_type = pr.get("llm_pr_type", pr.get("h_pr_type", "unknown"))
        has_snap = pr.get("h_has_snapshot_changes", "false") == "true"

        # Resume: skip already-mined PRs
        if pr_num in existing:
            results.append(existing[pr_num])
            continue

        print(f"[{i+1}/{len(post_adoption)}] PR #{pr_num}: {pr_title[:60]}...", end="", flush=True)

        commits = get_pr_commits(pr_num)
        if not commits:
            print(" no commits found")
            row = {
                "pr_number": pr_num,
                "pr_title": pr_title,
                "pr_type": pr_type,
                "has_snapshot_file_changes": has_snap,
                "num_commits": 0,
                "snapshot_ci_sequence": "",
                "first_commit_ci": "",
                "last_commit_ci": "",
                "ever_failed": False,
                "classification": "no_ci_data",
            }
            results.append(row)
            continue

        # For large PRs, only check first 3 and last 3 commits
        if len(commits) > 10:
            sample = commits[:3] + commits[-3:]
            sampled = True
        else:
            sample = commits
            sampled = False

        ci_results = []
        for sha in sample:
            result = get_snapshot_check_result(sha)
            ci_results.append(result or "none")
            time.sleep(0.05)

        sequence = ",".join(ci_results)
        if sampled:
            sequence += f" (sampled {len(sample)}/{len(commits)})"
        ever_failed = any(r == "failure" for r in ci_results)
        first_ci = ci_results[0] if ci_results else ""
        last_ci = ci_results[-1] if ci_results else ""
        classification = classify_pr(ever_failed, last_ci, has_snap)

        markers = {
            "resolved_code_fix": "🔧",
            "resolved_baseline": "📝",
            "merged_with_failure": "⚠️",
            "always_passed": "✓",
        }
        marker = markers.get(classification, "?")
        print(f" {len(commits)} commits | {sequence} | {classification} {marker}")

        row = {
            "pr_number": pr_num,
            "pr_title": pr_title,
            "pr_type": pr_type,
            "has_snapshot_file_changes": has_snap,
            "num_commits": len(commits),
            "snapshot_ci_sequence": sequence,
            "first_commit_ci": first_ci,
            "last_commit_ci": last_ci,
            "ever_failed": ever_failed,
            "classification": classification,
        }
        results.append(row)

        # Save incrementally every 10 PRs
        if (i + 1) % 10 == 0:
            save_results(results)

    save_results(results)
    print_summary(results)


if __name__ == "__main__":
    main()
