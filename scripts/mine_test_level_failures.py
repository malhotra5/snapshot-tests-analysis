#!/usr/bin/env python3
"""Mine test-level failure details from snapshot CI logs.

For each PR where snapshot CI ever failed, downloads the CI job log for
every failing commit and extracts the specific test names that failed.
Then cross-references with the PR's changed files to determine whether
each failing test was resolved by a baseline update or a code fix.

GitHub retains job logs for ~90 days, so older PRs will have expired
logs. The script records which PRs have available vs expired logs.

Outputs:
  data/test_level_failures.csv — one row per (PR, commit, test) triple:
    pr_number, commit_sha, job_id, log_status, failing_test,
    test_file, snapshot_dir_changed, resolution

  data/test_level_summary.csv — one row per PR:
    pr_number, pr_title, classification_v2, logs_available,
    num_failing_tests, num_resolved_baseline, num_resolved_code_fix,
    num_unresolved, refined_classification

Usage:
    export GITHUB_TOKEN=...
    python scripts/mine_test_level_failures.py
"""

import csv
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = "OpenHands/openhands-cli"
SNAPSHOT_JOB_NAME = "Run snapshot tests"
DATA_DIR = Path("data")
DETAIL_OUTPUT = DATA_DIR / "test_level_failures.csv"
SUMMARY_OUTPUT = DATA_DIR / "test_level_summary.csv"

# Snapshot SVG files live under tests/snapshots/ — each test module has
# a corresponding snapshot directory. E.g.:
#   tests/snapshots/e2e/test_app_initial_state.py
#     → tests/snapshots/e2e/test_app_initial_state/
SNAPSHOT_BASE = "tests/snapshots/"


def gh_api(endpoint):
    """Call GitHub API via gh CLI. Returns raw stdout."""
    result = subprocess.run(
        ["gh", "api", endpoint], capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    return result.stdout


def gh_api_json(endpoint, jq_filter=None):
    """Call GitHub API via gh CLI with optional jq filter."""
    cmd = ["gh", "api", endpoint]
    if jq_filter:
        cmd += ["--jq", jq_filter]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
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
        data = gh_api_json(
            f"repos/{REPO}/pulls/{pr_number}/commits?per_page=100&page={page}"
        )
        if not data:
            break
        commits.extend(c["sha"] for c in data)
        if len(data) < 100:
            break
        page += 1
    return commits


def get_failing_snapshot_jobs(sha):
    """Get (job_id, conclusion) for snapshot check-runs on a commit.
    Returns list of job_ids where conclusion == 'failure'."""
    data = gh_api_json(f"repos/{REPO}/commits/{sha}/check-runs")
    if not data or "check_runs" not in data:
        return []
    return [
        run["id"]
        for run in data["check_runs"]
        if run.get("name") == SNAPSHOT_JOB_NAME
        and run.get("conclusion") == "failure"
    ]


def get_job_log(job_id):
    """Download job log. Returns (log_text, status).
    status is 'available' or 'expired'."""
    raw = gh_api(f"repos/{REPO}/actions/jobs/{job_id}/logs")
    if raw is None or "410" in raw[:200]:
        return None, "expired"
    return raw, "available"


def extract_failing_tests(log_text):
    """Extract fully-qualified test names from pytest output in CI log.

    Looks for lines like:
      FAILED tests/snapshots/e2e/test_foo.py::TestFoo::test_bar[param] - Assert...
    or the short-form progress lines followed by test path.
    """
    # Match the summary-style FAILED lines at the end of pytest output
    pattern = re.compile(
        r"FAILED\s+(tests/\S+\.py::\S+)"
    )
    tests = set()
    for line in log_text.split("\n"):
        m = pattern.search(line)
        if m:
            tests.add(m.group(1))
    return sorted(tests)


def test_name_to_snapshot_dir(test_name):
    """Map a test name to its expected snapshot directory.

    E.g. 'tests/snapshots/e2e/test_app_initial_state.py::TestAppInitialState::test_app_initial_state'
      → 'tests/snapshots/e2e/test_app_initial_state/'
    """
    # Extract the file path portion before ::
    file_path = test_name.split("::")[0]
    # The snapshot dir is the same path minus .py extension
    snap_dir = file_path.replace(".py", "/")
    return snap_dir


def get_pr_changed_files(pr_number):
    """Get list of files changed in a PR."""
    files = []
    page = 1
    while True:
        data = gh_api_json(
            f"repos/{REPO}/pulls/{pr_number}/files?per_page=100&page={page}"
        )
        if not data:
            break
        files.extend(f["filename"] for f in data)
        if len(data) < 100:
            break
        page += 1
    return files


def check_snapshot_dir_changed(snap_dir, changed_files):
    """Check if any file under snap_dir was changed in the PR."""
    return any(f.startswith(snap_dir) for f in changed_files)


DETAIL_FIELDS = [
    "pr_number", "commit_sha", "job_id", "log_status",
    "failing_test", "test_file", "snapshot_dir",
    "snapshot_dir_changed", "resolution",
]

SUMMARY_FIELDS = [
    "pr_number", "pr_title", "classification_v2", "logs_available",
    "num_failing_commits_with_logs", "unique_failing_tests",
    "num_resolved_baseline", "num_resolved_code_fix",
    "refined_classification",
]


def main():
    # Load v2 CI results
    with open(DATA_DIR / "snapshot_ci_results_v2.csv") as f:
        ci_rows = {r["pr_number"]: r for r in csv.DictReader(f)}

    # Get all PRs where snapshot CI ever failed
    failed_prs = [
        r for r in ci_rows.values() if r["ever_failed"] == "True"
    ]
    failed_prs.sort(key=lambda r: int(r["pr_number"]), reverse=True)

    print(f"PRs where snapshot CI ever failed: {len(failed_prs)}")
    print(f"Mining test-level failure details from CI logs...\n")

    detail_rows = []
    summary_rows = []
    logs_available_count = 0
    logs_expired_count = 0
    cutoff_pr = None

    for pr in failed_prs:
        pr_num = pr["pr_number"]
        pr_title = pr["pr_title"]
        v2_class = pr["classification"]

        print(f"PR #{pr_num} [{v2_class}] {pr_title[:60]}")

        commits = get_pr_commits(pr_num)
        changed_files = get_pr_changed_files(pr_num)
        time.sleep(0.05)

        # Track per-PR state
        pr_has_any_logs = False
        pr_failing_tests = set()
        failing_commits_with_logs = 0

        for sha in commits:
            short = sha[:8]
            job_ids = get_failing_snapshot_jobs(sha)
            time.sleep(0.05)

            if not job_ids:
                continue

            for job_id in job_ids:
                log_text, log_status = get_job_log(job_id)
                time.sleep(0.05)

                if log_status == "expired":
                    detail_rows.append({
                        "pr_number": pr_num,
                        "commit_sha": short,
                        "job_id": str(job_id),
                        "log_status": "expired",
                        "failing_test": "",
                        "test_file": "",
                        "snapshot_dir": "",
                        "snapshot_dir_changed": "",
                        "resolution": "",
                    })
                    continue

                pr_has_any_logs = True
                failing_commits_with_logs += 1
                tests = extract_failing_tests(log_text)

                if not tests:
                    # Log available but couldn't parse test names
                    detail_rows.append({
                        "pr_number": pr_num,
                        "commit_sha": short,
                        "job_id": str(job_id),
                        "log_status": "available",
                        "failing_test": "(unparseable)",
                        "test_file": "",
                        "snapshot_dir": "",
                        "snapshot_dir_changed": "",
                        "resolution": "",
                    })
                    print(f"  {short}: logs available but no FAILED lines parsed")
                    continue

                for test in tests:
                    test_file = test.split("::")[0]
                    snap_dir = test_name_to_snapshot_dir(test)
                    dir_changed = check_snapshot_dir_changed(
                        snap_dir, changed_files
                    )
                    resolution = "baseline_updated" if dir_changed else "code_fixed"
                    pr_failing_tests.add((test, resolution))

                    detail_rows.append({
                        "pr_number": pr_num,
                        "commit_sha": short,
                        "job_id": str(job_id),
                        "log_status": "available",
                        "failing_test": test,
                        "test_file": test_file,
                        "snapshot_dir": snap_dir,
                        "snapshot_dir_changed": str(dir_changed),
                        "resolution": resolution,
                    })

                fail_summary = ", ".join(
                    t.split("::")[-1][:40] for t in tests
                )
                print(f"  {short}: {len(tests)} failing — {fail_summary}")

        # PR-level summary
        if pr_has_any_logs:
            logs_available_count += 1
            if cutoff_pr is None or int(pr_num) < int(cutoff_pr):
                cutoff_pr = pr_num
        else:
            logs_expired_count += 1

        # Deduplicate failing tests across commits (same test may fail
        # on multiple commits — use the resolution from final appearance)
        unique_tests = {}
        for test, resolution in pr_failing_tests:
            unique_tests[test] = resolution

        n_baseline = sum(1 for r in unique_tests.values() if r == "baseline_updated")
        n_code_fix = sum(1 for r in unique_tests.values() if r == "code_fixed")

        # Refined classification
        if not pr_has_any_logs:
            refined = f"logs_expired ({v2_class})"
        elif n_baseline > 0 and n_code_fix > 0:
            refined = "mixed (baseline + code fix)"
        elif n_baseline > 0:
            refined = "all_baseline_updated"
        elif n_code_fix > 0:
            refined = "all_code_fixed"
        else:
            refined = "no_tests_parsed"

        summary_rows.append({
            "pr_number": pr_num,
            "pr_title": pr_title,
            "classification_v2": v2_class,
            "logs_available": str(pr_has_any_logs),
            "num_failing_commits_with_logs": failing_commits_with_logs,
            "unique_failing_tests": len(unique_tests),
            "num_resolved_baseline": n_baseline,
            "num_resolved_code_fix": n_code_fix,
            "refined_classification": refined,
        })

        status = "📋" if pr_has_any_logs else "⏳"
        print(f"  {status} {refined} (baseline={n_baseline}, code_fix={n_code_fix})\n")

    # Save detail CSV
    with open(DETAIL_OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_FIELDS)
        writer.writeheader()
        writer.writerows(detail_rows)

    # Save summary CSV
    with open(SUMMARY_OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary_rows)

    # Print overall summary
    print(f"\n{'='*60}")
    print(f"Detail rows: {len(detail_rows)} → {DETAIL_OUTPUT}")
    print(f"Summary rows: {len(summary_rows)} → {SUMMARY_OUTPUT}")
    print(f"{'='*60}")
    print(f"PRs with logs available: {logs_available_count}")
    print(f"PRs with logs expired:   {logs_expired_count}")
    if cutoff_pr:
        print(f"Oldest PR with logs:     #{cutoff_pr}")
    print()

    print("Refined classifications:")
    from collections import Counter
    for cls, count in Counter(
        r["refined_classification"] for r in summary_rows
    ).most_common():
        print(f"  {cls}: {count}")


if __name__ == "__main__":
    main()
