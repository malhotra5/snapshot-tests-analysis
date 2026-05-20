#!/usr/bin/env python3
"""Generate a graph showing snapshot CI outcomes for post-adoption PRs.

Uses both PR-level (v2) and test-level data. For the 9 PRs with CI logs,
we know at the test level whether failing tests were resolved by code
fixes or baseline updates. For the remaining 20, we fall back to the
PR-level classification. PRs that did both (caught regressions AND
updated baselines for other tests) get their own category.
"""

import csv
import matplotlib.pyplot as plt
from pathlib import Path
from collections import Counter

DATA_DIR = Path("data")
OUT_DIR = Path("graphs")

C_BASELINE = "#2ecc71"     # green
C_CODE_FIX = "#3498db"     # blue
C_BOTH = "#8e44ad"         # purple
C_MERGED = "#e74c3c"       # red
C_FUNNEL_TOP = "#bdc3c7"   # gray
C_FUNNEL_BOT = "#f39c12"   # orange


def main():
    # Load v2 CI results
    with open(DATA_DIR / "snapshot_ci_results_v2.csv") as f:
        ci_rows = list(csv.DictReader(f))

    # Load test-level summary
    with open(DATA_DIR / "test_level_summary.csv") as f:
        test_level = {r["pr_number"]: r for r in csv.DictReader(f)}

    # Filter to PRs with snapshot CI data
    has_ci = [r for r in ci_rows
              if "failure" in r["snapshot_ci_sequence"]
              or "success" in r["snapshot_ci_sequence"]]

    failed = [r for r in has_ci if r["ever_failed"] == "True"]
    passed = [r for r in has_ci if r["ever_failed"] != "True"]

    # Classify each failed PR using test-level data where available
    n_baseline_only = 0
    n_code_fix = 0
    n_both = 0
    n_merged = 0

    for r in failed:
        pr_num = r["pr_number"]
        v2 = r["classification"]
        tl = test_level.get(pr_num, {})
        has_logs = tl.get("logs_available") == "True"
        n_cf = int(tl.get("num_resolved_code_fix", 0))
        n_bl = int(tl.get("num_resolved_baseline", 0))

        if v2 == "merged_with_failure":
            n_merged += 1
        elif has_logs and n_cf > 0:
            # Test-level data shows code fixes for failing tests
            if v2 == "resolved_baseline":
                # PR also updated baselines for other tests
                n_both += 1
            else:
                n_code_fix += 1
        elif v2 == "resolved_baseline":
            n_baseline_only += 1
        else:
            n_code_fix += 1

    total = n_baseline_only + n_code_fix + n_both + n_merged

    # --- Graph ---
    fig, (ax_funnel, ax_split) = plt.subplots(
        1, 2, figsize=(14, 5.5), gridspec_kw={"width_ratios": [1, 1.4]}
    )

    # Left panel: funnel
    stages = [
        (len(has_ci), "PRs with\nsnapshot CI", C_FUNNEL_TOP),
        (len(failed), "Triggered\nsnapshot failure", C_FUNNEL_BOT),
    ]
    bars = ax_funnel.barh(
        [s[1] for s in stages], [s[0] for s in stages],
        color=[s[2] for s in stages], height=0.5,
        edgecolor="white", linewidth=2,
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

    # Right panel: resolution breakdown with test-level adjustments
    labels = [
        "Updated baseline only\n(intentional change)",
        "Fixed code\n(regression caught)",
        "Both: fixed code +\nupdated other baselines",
        "Merged with failure\n(advisory CI ignored)",
    ]
    values = [n_baseline_only, n_code_fix, n_both, n_merged]
    colors = [C_BASELINE, C_CODE_FIX, C_BOTH, C_MERGED]

    bars = ax_split.barh(labels, values, color=colors, height=0.55,
                         edgecolor="white", linewidth=2)
    for bar, val in zip(bars, values):
        pct = 100 * val / total if total else 0
        ax_split.text(bar.get_width() + 0.3,
                      bar.get_y() + bar.get_height() / 2,
                      f"{val}  ({pct:.0f}%)", va="center",
                      fontsize=13, fontweight="bold")
    ax_split.set_xlim(0, max(values) * 1.5)
    ax_split.set_xlabel("Number of PRs")
    ax_split.invert_yaxis()
    ax_split.set_title(
        f"How {total} PRs With Snapshot Failures Resolved",
        fontsize=13, fontweight="bold",
    )
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
    print(f"\nOf {total} failures (test-level adjusted):")
    print(f"  {n_baseline_only:2d} ({100*n_baseline_only/total:.0f}%) Baseline only (unverified, logs expired)")
    print(f"  {n_code_fix:2d} ({100*n_code_fix/total:.0f}%) Code fixed")
    print(f"  {n_both:2d} ({100*n_both/total:.0f}%) Both (code fix + baseline update)")
    print(f"  {n_merged:2d} ({100*n_merged/total:.0f}%) Merged with failure")
    print(f"\n  Regression-catch rate: {n_code_fix + n_both} of {total}"
          f" ({100*(n_code_fix+n_both)/total:.0f}%) — likely higher,"
          f" {n_baseline_only} PRs unverifiable")


if __name__ == "__main__":
    main()
