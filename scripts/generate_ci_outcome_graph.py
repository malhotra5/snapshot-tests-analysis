#!/usr/bin/env python3
"""Generate a graph showing how snapshot CI failures were resolved,
broken down by PR type.

Uses test-level CI logs where available (9 PRs), falls back to
file-level heuristics for the remaining 20 PRs with expired logs.

Four resolution categories:
  - baseline_only: only snapshot baselines updated, no code changes
  - code_fix_only: only code changed, no snapshot file updates
  - both: baseline updated AND code changed in the same PR
  - merged: CI still failing when PR merged
"""

import csv
import json
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path("data")
OUT_DIR = Path("graphs")

C_BASELINE = "#2ecc71"   # green
C_CODE_FIX = "#3498db"   # blue
C_BOTH = "#9B59B6"       # purple
C_MERGED = "#e74c3c"     # red

CODE_EXTS = (".py", ".tcss", ".ts", ".js", ".css")

TYPE_GROUPS = {
    "feature": "Feature",
    "bug-fix": "Bug fix",
    "refactor": "Refactor",
    "dependency-bump": "Dep bump",
    "version-bump": "Version bump",
    "ci": "CI / Docs",
    "docs": "CI / Docs",
    "test": "Test",
}


def classify_pr(ci_row, test_level_row, pr_files):
    """Return 'baseline', 'code_fix', 'both', or 'merged' for a failed PR.

    Uses test-level logs when available, otherwise falls back to
    file-level heuristic (does the PR's final diff contain both
    snapshot files and code files?).
    """
    v2 = ci_row["classification"]
    if v2 == "merged_with_failure":
        return "merged"

    tl = test_level_row or {}
    has_logs = tl.get("logs_available") == "True"
    n_bl = int(tl.get("num_resolved_baseline", 0))
    n_cf = int(tl.get("num_resolved_code_fix", 0))

    if has_logs and (n_bl + n_cf) > 0:
        if n_bl > 0 and n_cf > 0:
            return "both"
        elif n_cf > 0:
            return "code_fix"
        else:
            return "baseline"

    # Fallback: file-level heuristic from PR diff
    snap_files = [f for f in pr_files
                  if "__snapshots__" in f.get("filename", "")
                  and f["filename"].endswith(".svg")]
    has_snap = len(snap_files) > 0
    has_code = any(f["filename"].endswith(CODE_EXTS) for f in pr_files
                   if "__snapshots__" not in f.get("filename", ""))

    if has_snap and has_code:
        return "both"
    elif has_snap:
        return "baseline"
    elif has_code:
        return "code_fix"
    # Edge case: only non-code, non-snapshot files (e.g. CI yaml)
    return "code_fix" if v2 == "resolved_code_fix" else "baseline"


def main():
    with open(DATA_DIR / "snapshot_ci_results_v2.csv") as f:
        ci_rows = list(csv.DictReader(f))
    with open(DATA_DIR / "categorized_prs.csv") as f:
        pr_data = {r["pr_number"]: r for r in csv.DictReader(f)}
    with open(DATA_DIR / "test_level_summary.csv") as f:
        test_level = {r["pr_number"]: r for r in csv.DictReader(f)}
    with open(DATA_DIR / "mined_prs.json") as f:
        mined = json.load(f)
    pr_files_map = {str(p["pr_number"]): p.get("pr_files", [])
                    for p in mined["pull_requests"]}

    has_ci = [r for r in ci_rows
              if "failure" in r["snapshot_ci_sequence"]
              or "success" in r["snapshot_ci_sequence"]]
    failed = [r for r in has_ci if r["ever_failed"] == "True"]

    type_baseline = defaultdict(int)
    type_code_fix = defaultdict(int)
    type_both = defaultdict(int)
    type_merged = defaultdict(int)

    for r in failed:
        pr_num = r["pr_number"]
        raw_type = pr_data.get(pr_num, {}).get("llm_pr_type", "other")
        group = TYPE_GROUPS.get(raw_type, "Other")
        cls = classify_pr(r, test_level.get(pr_num), pr_files_map.get(pr_num, []))

        if cls == "baseline":
            type_baseline[group] += 1
        elif cls == "code_fix":
            type_code_fix[group] += 1
        elif cls == "both":
            type_both[group] += 1
        else:
            type_merged[group] += 1

    all_types = sorted(
        set(list(type_baseline) + list(type_code_fix)
            + list(type_both) + list(type_merged)),
        key=lambda t: (type_baseline[t] + type_code_fix[t]
                       + type_both[t] + type_merged[t]),
        reverse=True,
    )

    bl_vals = [type_baseline[t] for t in all_types]
    cf_vals = [type_code_fix[t] for t in all_types]
    bo_vals = [type_both[t] for t in all_types]
    mg_vals = [type_merged[t] for t in all_types]

    # --- Graph ---
    fig, ax = plt.subplots(figsize=(11, 5))
    y = np.arange(len(all_types))
    bar_h = 0.6

    left = [0] * len(all_types)
    ax.barh(y, bl_vals, bar_h, left=left,
            label="Baseline only (intentional change)",
            color=C_BASELINE, edgecolor="white", linewidth=1.5)
    left = [a + b for a, b in zip(left, bl_vals)]

    ax.barh(y, bo_vals, bar_h, left=left,
            label="Both (baseline + code change)",
            color=C_BOTH, edgecolor="white", linewidth=1.5)
    left = [a + b for a, b in zip(left, bo_vals)]

    ax.barh(y, cf_vals, bar_h, left=left,
            label="Code fix only (regression caught)",
            color=C_CODE_FIX, edgecolor="white", linewidth=1.5)
    left = [a + b for a, b in zip(left, cf_vals)]

    ax.barh(y, mg_vals, bar_h, left=left,
            label="Merged with failure (CI advisory)",
            color=C_MERGED, edgecolor="white", linewidth=1.5)
    left = [a + b for a, b in zip(left, mg_vals)]

    for i in range(len(all_types)):
        ax.text(left[i] + 0.2, i, str(left[i]),
                va="center", fontsize=12, fontweight="bold")

    ax.set_yticks(y)
    ax.set_yticklabels(all_types, fontsize=11)
    ax.invert_yaxis()
    ax.set_xlabel("Number of PRs", fontsize=11)
    ax.set_title(
        f"How {len(failed)} PRs With Snapshot Failures Resolved, by Type",
        fontsize=13, fontweight="bold",
    )
    ax.legend(loc="lower right", fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(0, max(left) * 1.25)

    fig.tight_layout()
    out_path = OUT_DIR / "deep_19_ci_outcomes.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")

    total_bl = sum(bl_vals)
    total_cf = sum(cf_vals)
    total_bo = sum(bo_vals)
    total_mg = sum(mg_vals)
    total = total_bl + total_cf + total_bo + total_mg
    print(f"\n{total} PRs with snapshot failures:")
    print(f"  {total_bl} ({100*total_bl/total:.0f}%) baseline only")
    print(f"  {total_bo} ({100*total_bo/total:.0f}%) both (baseline + code)")
    print(f"  {total_cf} ({100*total_cf/total:.0f}%) code fix only")
    print(f"  {total_mg} ({100*total_mg/total:.0f}%) merged with failure")
    print(f"\nBy type:")
    for t in all_types:
        bl, bo, cf, mg = type_baseline[t], type_both[t], type_code_fix[t], type_merged[t]
        print(f"  {t:<14} baseline={bl} both={bo} code_fix={cf} merged={mg}")


if __name__ == "__main__":
    main()
