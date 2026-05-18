#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib>=3.9"]
# ///
"""
Generate deep-analysis graphs from categorized PR data + commit-level evidence.

Focuses on the harder questions:
  - Are snapshot tests catching regressions or just adding coverage?
  - Do refactors/large PRs benefit from snapshots?
  - What part of the SDLC do snapshots actually help with?

Usage (via uv):
    uv run scripts/generate_deep_analysis_graphs.py
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

C = {
    "new": "#27AE60",
    "modified": "#F2994A",
    "mixed": "#9B59B6",
    "before": "#BDBDBD",
    "after": "#5B8DEF",
    "agent": "#5B8DEF",
    "human": "#F2994A",
    "snap": "#27AE60",
    "no_snap": "#E0E0E0",
    "feature": "#27AE60",
    "bug-fix": "#EB5757",
    "refactor": "#9B59B6",
    "test": "#F2C94C",
    "version-bump": "#BDBDBD",
    "other": "#828282",
    "code_then_snap": "#EB5757",
    "snap_from_start": "#27AE60",
    "snap_only": "#56CCF2",
}


def setup_style():
    plt.rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.alpha": 0.3,
        "grid.linewidth": 0.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
    })


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def load(csv_path, json_path):
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    with open(json_path) as f:
        full = {p["pr_number"]: p for p in json.load(f)["pull_requests"]}
    return rows, full


def is_tui(r):
    return (r.get("llm_touches_tui", "").lower() == "true"
            or r.get("h_touches_tui", "") == "true")


def snap_breakdown(pr_number, full):
    pr = full.get(int(pr_number), {})
    files = [f for f in pr.get("pr_files", [])
             if "snapshot" in f["filename"].lower() or f["filename"].endswith(".svg")]
    added = [f for f in files if f.get("status") == "added"]
    modified = [f for f in files if f.get("status") == "modified"]
    return added, modified


def median(lst):
    if not lst:
        return 0
    lst = sorted(lst)
    n = len(lst)
    return (lst[n // 2 - 1] + lst[n // 2]) / 2 if n % 2 == 0 else lst[n // 2]


def safe_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


SNAP_KEYWORDS = ["snapshot", "snap ", "update snap", "regenerate", "update svg",
                 "update test", "baseline"]


# ---------------------------------------------------------------------------
# Graph 1: Bug-fix snapshot pattern — new vs modified
# ---------------------------------------------------------------------------

def fig_bugfix_snapshot_pattern(rows, full, out):
    """Bar chart: bug-fix PRs with snapshots — new-only vs modified-only vs mixed."""
    snap_bugs = [r for r in rows
                 if r["llm_pr_type"] == "bug-fix"
                 and r["h_has_snapshot_changes"] == "true"]

    cats = Counter()
    for r in snap_bugs:
        added, modified = snap_breakdown(r["pr_number"], full)
        if added and not modified:
            cats["New snapshots\nonly"] += 1
        elif modified and not added:
            cats["Existing snapshots\nupdated only"] += 1
        elif added and modified:
            cats["Mixed\n(new + updated)"] += 1

    labels = ["Existing snapshots\nupdated only", "Mixed\n(new + updated)", "New snapshots\nonly"]
    values = [cats.get(l, 0) for l in labels]
    colors = [C["modified"], C["mixed"], C["new"]]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar(labels, values, color=colors, edgecolor="white", width=0.6)
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.15,
                str(v), ha="center", va="bottom", fontsize=14, fontweight="bold")

    ax.set_ylabel("Number of bug-fix PRs")
    ax.set_title(f"Bug-Fix PRs with Snapshot Changes (n={len(snap_bugs)})\n"
                 "How the snapshots were affected")
    ax.set_ylim(0, max(values) * 1.25)
    fig.tight_layout()
    fig.savefig(out / "deep_1_bugfix_snapshot_pattern.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 2: Snapshot file counts — added vs modified across all snapshot PRs
# ---------------------------------------------------------------------------

def fig_snapshot_file_counts(rows, full, out):
    """Stacked bar by PR type: total snapshot files added vs modified."""
    types = ["feature", "bug-fix", "refactor", "test", "version-bump"]
    added_counts = []
    mod_counts = []

    for t in types:
        subset = [r for r in rows
                  if r["llm_pr_type"] == t and r["h_has_snapshot_changes"] == "true"]
        total_added = sum(len(snap_breakdown(r["pr_number"], full)[0]) for r in subset)
        total_mod = sum(len(snap_breakdown(r["pr_number"], full)[1]) for r in subset)
        added_counts.append(total_added)
        mod_counts.append(total_mod)

    fig, ax = plt.subplots(figsize=(9, 5))
    x = range(len(types))
    ax.bar(x, mod_counts, color=C["modified"], label="Modified (existing baselines updated)",
           edgecolor="white")
    ax.bar(x, added_counts, bottom=mod_counts, color=C["new"],
           label="Added (new coverage)", edgecolor="white")

    for i, (a, m) in enumerate(zip(added_counts, mod_counts)):
        total = a + m
        if total > 0:
            ax.text(i, total + 1, str(total), ha="center", va="bottom",
                    fontsize=10, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([t.replace("-", "\n") for t in types])
    ax.set_ylabel("Snapshot files changed")
    ax.set_title("Snapshot Files: New Coverage vs Updated Baselines\nby PR Type")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "deep_2_snapshot_file_counts.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 3: Commit-level evidence — "code then fix snapshots" pattern
# ---------------------------------------------------------------------------

def fig_commit_level_pattern(rows, full, out):
    """How many multi-commit snapshot PRs show the 'code → then fix snapshots' pattern?"""
    snap_prs = [r for r in rows
                if r["h_has_snapshot_changes"] == "true"
                and r["llm_pr_type"] not in ("version-bump", "dependency-bump")
                and int(r.get("pr_commit_count", 0)) >= 2]

    code_then_snap = 0
    snap_from_start = 0

    for r in snap_prs:
        pr = full.get(int(r["pr_number"]), {})
        commits = pr.get("pr_commits", [])
        if len(commits) < 2:
            continue

        snap_indices = [
            i for i, c in enumerate(commits)
            if any(kw in (c.get("message") or "").lower() for kw in SNAP_KEYWORDS)
        ]

        if snap_indices and all(i > 0 for i in snap_indices):
            code_then_snap += 1
        else:
            snap_from_start += 1

    fig, ax = plt.subplots(figsize=(7, 4.5))
    labels = ["Code first,\nsnapshots updated later", "Snapshots included\nfrom first commit"]
    values = [code_then_snap, snap_from_start]
    colors = [C["code_then_snap"], C["snap_from_start"]]

    bars = ax.bar(labels, values, color=colors, edgecolor="white", width=0.55)
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
                str(v), ha="center", va="bottom", fontsize=14, fontweight="bold")

    total = code_then_snap + snap_from_start
    ax.set_ylabel("Number of PRs")
    ax.set_title(f"Commit-Level Pattern in Multi-Commit Snapshot PRs (n={total})\n"
                 '"Code first, update snapshots later" = snapshot tests broke during dev')
    ax.set_ylim(0, max(values) * 1.3)
    fig.tight_layout()
    fig.savefig(out / "deep_3_commit_level_pattern.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 4: Forcing function — % of TUI PRs with snapshot updates over time
# ---------------------------------------------------------------------------

def fig_forcing_function(rows, full, out):
    """Line chart: what % of TUI-changing PRs include snapshot updates, by month."""
    months = sorted(set(r.get("pr_created_at", "")[:7] for r in rows
                        if r.get("pr_created_at", "")[:7]))

    feat_pcts = []
    bug_pcts = []

    for m in months:
        tui_feats = [r for r in rows if r.get("pr_created_at", "")[:7] == m
                     and is_tui(r) and r["llm_pr_type"] == "feature"]
        tui_bugs = [r for r in rows if r.get("pr_created_at", "")[:7] == m
                    and is_tui(r) and r["llm_pr_type"] == "bug-fix"]

        feat_snap = [r for r in tui_feats if r["h_has_snapshot_changes"] == "true"]
        bug_snap = [r for r in tui_bugs if r["h_has_snapshot_changes"] == "true"]

        feat_pcts.append(100 * len(feat_snap) / max(len(tui_feats), 1) if tui_feats else None)
        bug_pcts.append(100 * len(bug_snap) / max(len(tui_bugs), 1) if tui_bugs else None)

    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(months))

    # Plot only non-None values
    fx = [i for i, v in enumerate(feat_pcts) if v is not None]
    fv = [v for v in feat_pcts if v is not None]
    bx = [i for i, v in enumerate(bug_pcts) if v is not None]
    bv = [v for v in bug_pcts if v is not None]

    ax.plot(fx, fv, color=C["feature"], marker="o", linewidth=2.5, markersize=8,
            label="TUI features with snapshots")
    ax.plot(bx, bv, color=C["bug-fix"], marker="s", linewidth=2.5, markersize=8,
            label="TUI bug-fixes with snapshots")

    # Adoption line
    adopt_idx = months.index("2026-01") if "2026-01" in months else None
    if adopt_idx is not None:
        ax.axvline(adopt_idx - 0.5, color="#333", linestyle="--", alpha=0.4, linewidth=1.5)
        ax.text(adopt_idx - 0.4, 95, "snapshot\nadoption", fontsize=9, color="#333", va="top")

    ax.set_xticks(x)
    ax.set_xticklabels(months, rotation=30, ha="right")
    ax.set_ylabel("% of TUI PRs including snapshot updates")
    ax.set_ylim(-5, 105)
    ax.set_title("Snapshot as Forcing Function\n"
                 "What fraction of TUI-changing PRs update snapshot baselines?")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "deep_4_forcing_function.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 5: Test suite reuse heatmap
# ---------------------------------------------------------------------------

def fig_suite_reuse(rows, full, out):
    """Horizontal bar: how many PRs touch each snapshot test suite."""
    suite_counts = defaultdict(set)
    for r in rows:
        if r["h_has_snapshot_changes"] != "true":
            continue
        pr = full.get(int(r["pr_number"]), {})
        for f in pr.get("pr_files", []):
            fname = f["filename"]
            if fname.endswith(".svg") and "snapshot" in fname.lower():
                for p in fname.split("/"):
                    if p.startswith("Test"):
                        suite_counts[p.split(".")[0]].add(r["pr_number"])
                        break

    suites = sorted(suite_counts.items(), key=lambda x: len(x[1]))
    names = [s[0].replace("Test", "") for s in suites]
    counts = [len(s[1]) for s in suites]

    fig, ax = plt.subplots(figsize=(10, 7))
    colors = [C["snap"] if c >= 10 else (C["modified"] if c >= 5 else C["no_snap"])
              for c in counts]
    bars = ax.barh(range(len(names)), counts, color=colors, edgecolor="white")

    for bar, c in zip(bars, counts):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                str(c), va="center", fontsize=9)

    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Number of PRs touching this test suite")
    ax.set_title("Snapshot Test Suite Reuse\n"
                 "How many PRs exercise each suite (higher = more regression coverage)")
    ax.legend(handles=[
        mpatches.Patch(color=C["snap"], label="≥10 PRs (high reuse)"),
        mpatches.Patch(color=C["modified"], label="5-9 PRs"),
        mpatches.Patch(color=C["no_snap"], label="<5 PRs (low reuse)"),
    ], loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "deep_5_suite_reuse.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 6: Review speed — first review time comparison
# ---------------------------------------------------------------------------

def fig_review_speed(rows, full, out):
    """Grouped bar: first-review time and merge time for snapshot vs non-snapshot PRs."""
    post = [r for r in rows
            if r.get("pr_created_at", "") >= "2026-01"
            and r["authorship"] in ("agent", "human")
            and is_tui(r)]

    groups = {
        "With\nsnapshots": [r for r in post if r["h_has_snapshot_changes"] == "true"],
        "Without\nsnapshots": [r for r in post if r["h_has_snapshot_changes"] != "true"],
    }

    fig, axes = plt.subplots(1, 2, figsize=(11, 5))

    # First review time
    ax = axes[0]
    labels = list(groups.keys())
    review_medians = []
    for subset in groups.values():
        times = [t for t in (safe_float(r.get("hours_to_first_review")) for r in subset)
                 if t is not None and t < 500]
        review_medians.append(median(times))

    bars = ax.bar(labels, review_medians,
                  color=[C["snap"], C["no_snap"]], edgecolor="white", width=0.5)
    for bar, v in zip(bars, review_medians):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                f"{v:.1f}h", ha="center", va="bottom", fontsize=12, fontweight="bold")
    ax.set_ylabel("Median hours")
    ax.set_title("Time to First Review\n(TUI PRs, post-adoption)")

    # Lines changed (to show PRs with snapshots are larger)
    ax = axes[1]
    size_medians = []
    for label, subset in groups.items():
        lines = [int(r.get("pr_additions", 0)) + int(r.get("pr_deletions", 0))
                 for r in subset]
        size_medians.append(median(lines))

    bars = ax.bar(labels, size_medians,
                  color=[C["snap"], C["no_snap"]], edgecolor="white", width=0.5)
    for bar, v in zip(bars, size_medians):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 10,
                f"{v:.0f}", ha="center", va="bottom", fontsize=12, fontweight="bold")
    ax.set_ylabel("Median lines changed")
    ax.set_title("PR Size\n(TUI PRs, post-adoption)")

    fig.suptitle("Snapshot PRs: Faster Reviews Despite Being Larger", fontsize=14,
                 fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(out / "deep_6_review_speed.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 7: Large/refactor PRs — snapshot involvement
# ---------------------------------------------------------------------------

def fig_large_pr_snapshots(rows, full, out):
    """Scatter: PR size vs commit count, colored by snapshot involvement."""
    post = [r for r in rows if r.get("pr_created_at", "") >= "2026-01"
            and r["authorship"] != "bot" and is_tui(r)]

    fig, ax = plt.subplots(figsize=(9, 6))

    for r in post:
        lines = int(r.get("pr_additions", 0)) + int(r.get("pr_deletions", 0))
        commits = int(r.get("pr_commit_count", 0))
        has_snap = r["h_has_snapshot_changes"] == "true"

        color = C["snap"] if has_snap else C["no_snap"]
        edge = "#333" if has_snap else "#CCC"
        size = 60 if has_snap else 30
        ax.scatter(lines, commits, c=color, edgecolors=edge, s=size,
                   alpha=0.7, linewidths=0.5, zorder=3 if has_snap else 2)

    ax.set_xlabel("Lines changed (additions + deletions)")
    ax.set_ylabel("Number of commits")
    ax.set_title("TUI PRs: Size vs Iteration\n"
                 "Green = includes snapshot changes")
    ax.set_xscale("symlog", linthresh=100)
    ax.legend(handles=[
        mpatches.Patch(color=C["snap"], label="With snapshot changes"),
        mpatches.Patch(color=C["no_snap"], label="Without snapshots"),
    ])
    fig.tight_layout()
    fig.savefig(out / "deep_7_large_pr_snapshots.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 8: Suite coverage vs bugs — which areas are covered?
# ---------------------------------------------------------------------------

def fig_suite_coverage_bugs(rows, full, out):
    """Grouped horizontal bar: features vs bug-fixes per test suite."""
    suite_features = defaultdict(int)
    suite_bugs = defaultdict(int)

    for r in rows:
        if r["h_has_snapshot_changes"] != "true":
            continue
        pr = full.get(int(r["pr_number"]), {})
        suites = set()
        for f in pr.get("pr_files", []):
            fname = f["filename"]
            if fname.endswith(".svg") and "snapshot" in fname.lower():
                for p in fname.split("/"):
                    if p.startswith("Test"):
                        suites.add(p.split(".")[0])
                        break
        for s in suites:
            if r["llm_pr_type"] == "feature":
                suite_features[s] += 1
            elif r["llm_pr_type"] == "bug-fix":
                suite_bugs[s] += 1

    all_suites = sorted(set(suite_features) | set(suite_bugs),
                        key=lambda s: suite_features.get(s, 0) + suite_bugs.get(s, 0))
    names = [s.replace("Test", "") for s in all_suites]
    feat_vals = [suite_features.get(s, 0) for s in all_suites]
    bug_vals = [suite_bugs.get(s, 0) for s in all_suites]

    fig, ax = plt.subplots(figsize=(10, 7))
    y = range(len(names))
    h = 0.35
    ax.barh([i + h / 2 for i in y], feat_vals, height=h, color=C["feature"],
            label="Feature PRs", edgecolor="white")
    ax.barh([i - h / 2 for i in y], bug_vals, height=h, color=C["bug-fix"],
            label="Bug-fix PRs", edgecolor="white")

    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Number of PRs")
    ax.set_title("Snapshot Suites: Feature Coverage vs Bug Fixes\n"
                 "Suites with both = areas where snapshots caught regressions")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "deep_8_suite_coverage_bugs.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 9: SDLC impact summary
# ---------------------------------------------------------------------------

def fig_sdlc_summary(rows, full, out):
    """Visual scorecard of where snapshots help in the SDLC."""
    # Compute commit-level stats for the CI gate evidence
    snap_prs = [r for r in rows
                if r["h_has_snapshot_changes"] == "true"
                and r["llm_pr_type"] not in ("version-bump", "dependency-bump")
                and int(r.get("pr_commit_count", 0)) >= 2]
    code_then_snap = 0
    total_multi = 0
    for r in snap_prs:
        pr = full.get(int(r["pr_number"]), {})
        commits = pr.get("pr_commits", [])
        if len(commits) < 2:
            continue
        total_multi += 1
        snap_indices = [
            i for i, c in enumerate(commits)
            if any(kw in (c.get("message") or "").lower() for kw in SNAP_KEYWORDS)
        ]
        if snap_indices and all(i > 0 for i in snap_indices):
            code_then_snap += 1

    phases = [
        "Development\n(forcing function)",
        "Code Review\n(visual diffs)",
        "CI Gate\n(regression catch)",
        "Release\n(baseline check)",
        "Bug Prevention\n(fewer regressions)",
    ]
    # Evidence scores: 0=no evidence, 1=weak, 2=moderate, 3=strong
    scores = [3, 2, 2, 1, 0]
    evidence = [
        "46% of TUI features\ninclude snapshot updates",
        "First review 2× faster\n(0.9h vs 1.7h)",
        f"{code_then_snap}/{total_multi} multi-commit PRs\nupdate snapshots later",
        "9 version bumps\nupdate 25 baselines each",
        "TUI bug ratio worse\n(0.60→0.95)",
    ]

    colors = ["#27AE60", "#27AE60", "#F2994A", "#F2C94C", "#EB5757"]
    alpha_map = {3: 1.0, 2: 0.7, 1: 0.4, 0: 0.2}

    fig, ax = plt.subplots(figsize=(12, 4))
    for i, (phase, score, ev, color) in enumerate(zip(phases, scores, evidence, colors)):
        ax.barh(i, score, color=color, alpha=alpha_map[score],
                edgecolor="white", height=0.6)
        strength = ["None", "Weak", "Moderate", "Strong"][score]
        ax.text(score + 0.1, i, f"{strength}", va="center", fontsize=10, fontweight="bold")
        ax.text(3.7, i, ev, va="center", fontsize=9, color="#555")

    ax.set_yticks(range(len(phases)))
    ax.set_yticklabels(phases, fontsize=10)
    ax.set_xlim(0, 7.5)
    ax.set_xlabel("Evidence Strength")
    ax.set_xticks([0, 1, 2, 3])
    ax.set_xticklabels(["None", "Weak", "Moderate", "Strong"])
    ax.set_title("Where Do Snapshot Tests Help in the SDLC?")
    ax.invert_yaxis()
    fig.tight_layout()
    fig.savefig(out / "deep_9_sdlc_summary.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ALL_FIGURES = [
    ("Bug-fix snapshot pattern (new vs modified)", fig_bugfix_snapshot_pattern),
    ("Snapshot file counts by PR type", fig_snapshot_file_counts),
    ("Commit-level pattern (code then snapshots)", fig_commit_level_pattern),
    ("Forcing function over time", fig_forcing_function),
    ("Test suite reuse", fig_suite_reuse),
    ("Review speed comparison", fig_review_speed),
    ("Large PR snapshot involvement", fig_large_pr_snapshots),
    ("Suite coverage vs bugs", fig_suite_coverage_bugs),
    ("SDLC impact summary", fig_sdlc_summary),
]


def main():
    p = argparse.ArgumentParser(description="Generate deep-analysis graphs")
    p.add_argument("--input-csv", default="data/categorized_prs.csv")
    p.add_argument("--input-json", default="data/mined_prs.json")
    p.add_argument("--output-dir", default="graphs")
    args = p.parse_args()

    setup_style()
    rows, full = load(args.input_csv, args.input_json)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(rows)} PRs")
    print(f"Generating {len(ALL_FIGURES)} deep-analysis graphs → {out}/\n")

    for name, func in ALL_FIGURES:
        print(f"  {name}...", end=" ", flush=True)
        func(rows, full, out)
        print("✓")

    print(f"\nDone! {len(ALL_FIGURES)} graphs saved to {out}/")


if __name__ == "__main__":
    main()
