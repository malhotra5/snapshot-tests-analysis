#!/usr/bin/env python3
"""Generate a graph showing how snapshot CI failures were resolved,
broken down by PR type.

Uses both PR-level (v2) and test-level data where CI logs were
available. Falls back to PR-level classification for older PRs.
"""

import csv
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path("data")
OUT_DIR = Path("graphs")

C_BASELINE = "#2ecc71"   # green
C_CODE_FIX = "#3498db"   # blue
C_MERGED = "#e74c3c"     # red

# Group small types for readability
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


def classify_pr(ci_row, test_level_row):
    """Return 'baseline', 'code_fix', or 'merged' for a failed PR."""
    v2 = ci_row["classification"]
    if v2 == "merged_with_failure":
        return "merged"

    tl = test_level_row or {}
    has_logs = tl.get("logs_available") == "True"
    n_cf = int(tl.get("num_resolved_code_fix", 0))

    if has_logs and n_cf > 0:
        return "code_fix"
    elif v2 == "resolved_code_fix":
        return "code_fix"
    else:
        return "baseline"


def main():
    with open(DATA_DIR / "snapshot_ci_results_v2.csv") as f:
        ci_rows = list(csv.DictReader(f))
    with open(DATA_DIR / "categorized_prs.csv") as f:
        pr_data = {r["pr_number"]: r for r in csv.DictReader(f)}
    with open(DATA_DIR / "test_level_summary.csv") as f:
        test_level = {r["pr_number"]: r for r in csv.DictReader(f)}

    has_ci = [r for r in ci_rows
              if "failure" in r["snapshot_ci_sequence"]
              or "success" in r["snapshot_ci_sequence"]]
    failed = [r for r in has_ci if r["ever_failed"] == "True"]

    # Classify each failed PR and group by type
    type_baseline = defaultdict(int)
    type_code_fix = defaultdict(int)
    type_merged = defaultdict(int)

    for r in failed:
        pr_num = r["pr_number"]
        raw_type = pr_data.get(pr_num, {}).get("llm_pr_type", "other")
        group = TYPE_GROUPS.get(raw_type, "Other")
        cls = classify_pr(r, test_level.get(pr_num))

        if cls == "baseline":
            type_baseline[group] += 1
        elif cls == "code_fix":
            type_code_fix[group] += 1
        else:
            type_merged[group] += 1

    # Order types by total count descending
    all_types = sorted(
        set(list(type_baseline) + list(type_code_fix) + list(type_merged)),
        key=lambda t: type_baseline[t] + type_code_fix[t] + type_merged[t],
        reverse=True,
    )

    baseline_vals = [type_baseline[t] for t in all_types]
    code_fix_vals = [type_code_fix[t] for t in all_types]
    merged_vals = [type_merged[t] for t in all_types]

    # --- Graph ---
    fig, ax = plt.subplots(figsize=(11, 5))
    y = np.arange(len(all_types))
    bar_h = 0.6

    bars_bl = ax.barh(y, baseline_vals, bar_h,
                      label="Updated baseline (intentional change)",
                      color=C_BASELINE, edgecolor="white", linewidth=1.5)
    bars_cf = ax.barh(y, code_fix_vals, bar_h, left=baseline_vals,
                      label="Fixed code (regression caught)",
                      color=C_CODE_FIX, edgecolor="white", linewidth=1.5)
    bars_mg = ax.barh(y, merged_vals, bar_h,
                      left=[b + c for b, c in zip(baseline_vals, code_fix_vals)],
                      label="Merged with failure (CI advisory)",
                      color=C_MERGED, edgecolor="white", linewidth=1.5)

    # Annotate totals
    for i, t in enumerate(all_types):
        total = baseline_vals[i] + code_fix_vals[i] + merged_vals[i]
        ax.text(total + 0.2, i, str(total),
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
    ax.set_xlim(0, max(b + c + m for b, c, m
                       in zip(baseline_vals, code_fix_vals, merged_vals)) * 1.25)

    fig.tight_layout()
    out_path = OUT_DIR / "deep_19_ci_outcomes.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")

    # Print summary
    total_bl = sum(baseline_vals)
    total_cf = sum(code_fix_vals)
    total_mg = sum(merged_vals)
    total = total_bl + total_cf + total_mg
    print(f"\n{total} PRs with snapshot failures:")
    print(f"  {total_bl} ({100*total_bl/total:.0f}%) updated baseline")
    print(f"  {total_cf} ({100*total_cf/total:.0f}%) fixed code")
    print(f"  {total_mg} ({100*total_mg/total:.0f}%) merged with failure")
    print(f"\nBy type:")
    for t in all_types:
        bl, cf, mg = type_baseline[t], type_code_fix[t], type_merged[t]
        print(f"  {t:<14} baseline={bl} code_fix={cf} merged={mg}")


if __name__ == "__main__":
    main()
