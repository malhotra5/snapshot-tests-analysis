#!/usr/bin/env python3
"""Generate a graph showing snapshot CI outcomes for post-adoption PRs."""

import csv
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path
from collections import Counter

DATA_DIR = Path("data")
OUT_DIR = Path("graphs")

C = {
    "resolved_baseline": "#2ecc71",   # green
    "resolved_code_fix": "#3498db",   # blue
    "merged_with_failure": "#e74c3c", # red
    "always_passed": "#ecf0f1",       # light gray
}


def main():
    # Load v2 CI results
    with open(DATA_DIR / "snapshot_ci_results_v2.csv") as f:
        ci_rows = list(csv.DictReader(f))

    # Load PR metadata for TUI flag
    with open(DATA_DIR / "categorized_prs.csv") as f:
        pr_data = {r["pr_number"]: r for r in csv.DictReader(f)}

    # Filter to PRs that had snapshot CI (not all "none")
    has_ci = [r for r in ci_rows
              if "failure" in r["snapshot_ci_sequence"]
              or "success" in r["snapshot_ci_sequence"]]

    failed = [r for r in has_ci if r["ever_failed"] == "True"]
    passed = [r for r in has_ci if r["ever_failed"] != "True"]

    counts = Counter(r["classification"] for r in failed)

    # --- Graph: What happens when snapshot CI fails? ---
    fig, (ax_funnel, ax_split) = plt.subplots(1, 2, figsize=(14, 5),
                                               gridspec_kw={"width_ratios": [1, 1.3]})

    # Left panel: funnel
    stages = [
        (len(has_ci), "PRs with\nsnapshot CI", "#bdc3c7"),
        (len(failed), "Triggered\nsnapshot failure", "#f39c12"),
    ]
    bars = ax_funnel.barh(
        [s[1] for s in stages],
        [s[0] for s in stages],
        color=[s[2] for s in stages],
        height=0.5,
        edgecolor="white",
        linewidth=2,
    )
    for bar, (count, _, _) in zip(bars, stages):
        ax_funnel.text(bar.get_width() + 2, bar.get_y() + bar.get_height() / 2,
                       str(count), va="center", fontsize=14, fontweight="bold")
    ax_funnel.set_xlim(0, max(s[0] for s in stages) * 1.2)
    ax_funnel.set_xlabel("Number of PRs")
    ax_funnel.invert_yaxis()
    ax_funnel.set_title("Snapshot CI Coverage", fontsize=13, fontweight="bold")
    ax_funnel.spines["top"].set_visible(False)
    ax_funnel.spines["right"].set_visible(False)

    # Right panel: how were the 29 failures resolved?
    labels = [
        "Updated baseline\n(intentional change)",
        "Fixed code\n(regression caught)",
        "Merged with failure\n(advisory CI ignored)",
    ]
    values = [
        counts.get("resolved_baseline", 0),
        counts.get("resolved_code_fix", 0),
        counts.get("merged_with_failure", 0),
    ]
    colors = [C["resolved_baseline"], C["resolved_code_fix"], C["merged_with_failure"]]

    bars = ax_split.barh(labels, values, color=colors, height=0.55,
                         edgecolor="white", linewidth=2)
    for bar, val in zip(bars, values):
        pct = 100 * val / sum(values)
        ax_split.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                      f"{val}  ({pct:.0f}%)", va="center", fontsize=13, fontweight="bold")
    ax_split.set_xlim(0, max(values) * 1.4)
    ax_split.set_xlabel("Number of PRs")
    ax_split.invert_yaxis()
    ax_split.set_title(f"How {sum(values)} Snapshot Failures Were Resolved",
                       fontsize=13, fontweight="bold")
    ax_split.spines["top"].set_visible(False)
    ax_split.spines["right"].set_visible(False)

    fig.tight_layout(w_pad=4)
    out_path = OUT_DIR / "deep_19_ci_outcomes.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")

    # Print summary
    print(f"\nOf {len(has_ci)} PRs with snapshot CI data:")
    print(f"  {len(passed)} ({100*len(passed)/len(has_ci):.0f}%) always passed")
    print(f"  {len(failed)} ({100*len(failed)/len(has_ci):.0f}%) had at least one failure")
    print(f"\nOf {len(failed)} failures:")
    for cls, label in [("resolved_baseline", "Updated baseline"),
                       ("resolved_code_fix", "Fixed code"),
                       ("merged_with_failure", "Merged with failure")]:
        n = counts.get(cls, 0)
        print(f"  {n:2d} ({100*n/len(failed):.0f}%) {label}")


if __name__ == "__main__":
    main()
