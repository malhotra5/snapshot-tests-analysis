#!/usr/bin/env python3
"""Generate a graph comparing PR-level vs test-level classification
for PRs where CI logs were available.

Story: PR-level classification sees a PR with snapshot file changes and
calls it "baseline updated." But the failing tests and the updated
baselines can be for different tests. When we cross-reference at the test
level, regressions caught jumps from 44% to 100% in our sample.
"""

import csv
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

DATA_DIR = Path("data")
OUT_DIR = Path("graphs")

C_CODE_FIX = "#3498db"
C_BASELINE = "#2ecc71"
C_MERGED = "#95a5a6"


def main():
    with open(DATA_DIR / "test_level_summary.csv") as f:
        rows = list(csv.DictReader(f))

    with_logs = [r for r in rows if r["logs_available"] == "True"]

    # Only PRs with parseable failing tests (exclude no_tests_parsed and
    # the 3 merged-with-failure which are a separate category)
    parseable = [r for r in with_logs
                 if int(r["unique_failing_tests"]) > 0
                 and r["classification_v2"] != "merged_with_failure"]

    # Sort by PR number ascending for timeline order
    parseable.sort(key=lambda r: int(r["pr_number"]))

    # --- Per-PR horizontal bar chart ---
    fig, ax = plt.subplots(figsize=(11, 5))

    labels = []
    code_fix_counts = []
    baseline_counts = []
    reclassified = []

    for r in parseable:
        pr = r["pr_number"]
        v2 = r["classification_v2"]
        n_cf = int(r["num_resolved_code_fix"])
        n_bl = int(r["num_resolved_baseline"])
        was_wrong = (v2 == "resolved_baseline" and n_cf > 0 and n_bl == 0)

        tag = " ← was 'baseline updated'" if was_wrong else ""
        labels.append(f"#{pr}{tag}")
        code_fix_counts.append(n_cf)
        baseline_counts.append(n_bl)
        reclassified.append(was_wrong)

    y = np.arange(len(labels))
    bar_height = 0.55

    bars_bl = ax.barh(y, baseline_counts, bar_height,
                      label="Baseline updated", color=C_BASELINE,
                      edgecolor="white", linewidth=1.5)
    bars_cf = ax.barh(y, code_fix_counts, bar_height, left=baseline_counts,
                      label="Code fixed (regression caught)", color=C_CODE_FIX,
                      edgecolor="white", linewidth=1.5)

    # Annotate counts
    for i, (cf, bl) in enumerate(zip(code_fix_counts, baseline_counts)):
        total = cf + bl
        ax.text(total + 0.4, i, str(total),
                va="center", fontsize=11, fontweight="bold")

    # Highlight reclassified rows
    for i, is_recl in enumerate(reclassified):
        if is_recl:
            ax.get_yticklabels()[i].set_color("#c0392b")
            ax.get_yticklabels()[i].set_fontweight("bold")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("Number of failing snapshot tests", fontsize=11)
    ax.set_title(
        "How Failing Snapshot Tests Were Actually Resolved\n"
        "(9 PRs with CI logs available, March–May 2026)",
        fontsize=13, fontweight="bold",
    )
    ax.legend(loc="lower right", fontsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(0, max(cf + bl for cf, bl in zip(code_fix_counts, baseline_counts)) * 1.15)

    fig.tight_layout()
    out_path = OUT_DIR / "deep_21_test_level_vs_pr_level.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
