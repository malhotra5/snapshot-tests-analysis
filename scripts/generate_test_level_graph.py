#!/usr/bin/env python3
"""Generate a graph comparing PR-level vs test-level classification
for PRs where CI logs were available.

Story: PR-level classification sees a PR with snapshot file changes and
calls it "baseline updated." But a single PR can update baselines for
some tests (intentional changes) while fixing code for others (regressions
caught). The PR-level view collapses both into one label. At the test
level, every failing test in our sample was resolved by a code fix.
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
        # These PRs also updated baselines for OTHER tests — the PR-level
        # view only saw the baseline updates and missed the code fixes
        is_both = (v2 == "resolved_baseline" and n_cf > 0)

        tag = " (+ baselines for other tests)" if is_both else ""
        labels.append(f"#{pr}{tag}")
        code_fix_counts.append(n_cf)
        baseline_counts.append(n_bl)
        reclassified.append(is_both)

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

    # Highlight PRs that were both regression-catch and baseline-update
    for i, is_both in enumerate(reclassified):
        if is_both:
            ax.get_yticklabels()[i].set_color("#8e44ad")
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
