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
# Graph 4: Forcing function — % of UI PRs with snapshot updates over time
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
            label="UI features with snapshots")
    ax.plot(bx, bv, color=C["bug-fix"], marker="s", linewidth=2.5, markersize=8,
            label="UI bug-fixes with snapshots")

    # Adoption line
    adopt_idx = months.index("2026-01") if "2026-01" in months else None
    if adopt_idx is not None:
        ax.axvline(adopt_idx - 0.5, color="#333", linestyle="--", alpha=0.4, linewidth=1.5)
        ax.text(adopt_idx - 0.4, 95, "snapshot\nadoption", fontsize=9, color="#333", va="top")

    ax.set_xticks(x)
    ax.set_xticklabels(months, rotation=30, ha="right")
    ax.set_ylabel("% of UI PRs including snapshot updates")
    ax.set_ylim(-5, 105)
    ax.set_title("Snapshot as Forcing Function")
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
    ax.set_title("Time to First Review\n(UI PRs, post-adoption)")

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
    ax.set_title("PR Size\n(UI PRs, post-adoption)")

    fig.suptitle("Time to First Review vs PR Size", fontsize=14,
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
    ax.set_title("UI PRs: Size vs Iteration\n"
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
        "46% of UI features\ninclude snapshot updates",
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
# Graph 10: Bug rate with codebase growth context
# ---------------------------------------------------------------------------

def fig_bug_rate_with_context(rows, full, out):
    """UX bugs normalized by cumulative UI features — the honest trend."""
    months = sorted(set(r.get("pr_created_at", "")[:7] for r in rows
                        if r.get("pr_created_at", "")[:7]))

    ux_per_feat = []
    cum_tui = 0
    sample_sizes = []

    for m in months:
        agent = [r for r in rows if r.get("pr_created_at", "")[:7] == m
                 and r["authorship"] == "agent"]
        ux = [r for r in agent if r["llm_pr_type"] == "bug-fix"
              and r["llm_bug_severity"] == "ux-regression"]
        feats = [r for r in agent if r["llm_pr_type"] == "feature" and is_tui(r)]
        cum_tui += len(feats)
        ux_per_feat.append(len(ux) / max(cum_tui, 1))
        sample_sizes.append(len(agent))

    fig, ax = plt.subplots(figsize=(10, 5.5))
    x = range(len(months))

    # Color pre vs post differently
    adopt_idx = months.index("2026-01") if "2026-01" in months else len(months)
    pre_x = list(range(adopt_idx))
    post_x = list(range(adopt_idx, len(months)))
    pre_y = ux_per_feat[:adopt_idx]
    post_y = ux_per_feat[adopt_idx:]

    ax.plot(pre_x, pre_y, color="#BDBDBD", marker="o", linewidth=2.5,
            markersize=8, label="Pre-adoption", zorder=3)
    ax.plot(post_x, post_y, color="#F2994A", marker="o", linewidth=2.5,
            markersize=8, label="Post-adoption", zorder=3)
    ax.fill_between(post_x, post_y, alpha=0.1, color="#F2994A")

    # Add sample size annotations
    for i, (v, n) in enumerate(zip(ux_per_feat, sample_sizes)):
        if n < 10:
            ax.annotate(f"n={n}", xy=(i, v), xytext=(0, 12),
                        textcoords="offset points", fontsize=8, color="#999",
                        ha="center")

    if adopt_idx < len(months):
        ax.axvline(adopt_idx - 0.5, color="#333", linestyle="--", alpha=0.4, linewidth=1.5)
        ax.text(adopt_idx - 0.4, max(ux_per_feat) * 0.95,
                "snapshot\nadoption", fontsize=9, color="#333", va="top")

    ax.set_xticks(x)
    ax.set_xticklabels(months, rotation=30, ha="right")
    ax.set_ylabel("UX-regression bugs / cumulative UI features", fontsize=10)
    ax.set_title("UX Regression Rate Normalized by Codebase Size")
    ax.legend(fontsize=10)
    ax.set_ylim(-0.02, max(ux_per_feat) * 1.25)

    fig.tight_layout()
    fig.savefig(out / "deep_10_bug_rate_with_context.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 11: UX regressions breakdown — where do they land?
# ---------------------------------------------------------------------------

def fig_ux_regression_breakdown(rows, full, out):
    """Stacked bar: UX-regression bug-fixes split by snapshot involvement."""
    months = sorted(set(r.get("pr_created_at", "")[:7] for r in rows
                        if r.get("pr_created_at", "")[:7]))

    snap_mod = []     # TUI, existing snapshots updated
    snap_new = []     # TUI, new snapshots added
    tui_no_snap = []  # TUI, no snapshot involvement
    non_tui = []      # not TUI at all

    for m in months:
        ux = [r for r in rows if r.get("pr_created_at", "")[:7] == m
              and r["llm_pr_type"] == "bug-fix" and r["llm_bug_severity"] == "ux-regression"]

        sm, sn, tns, nt = 0, 0, 0, 0
        for r in ux:
            tui = is_tui(r)
            has_snap = r["h_has_snapshot_changes"] == "true"
            if not tui:
                nt += 1
            elif not has_snap:
                tns += 1
            else:
                added, modified = snap_breakdown(r["pr_number"], full)
                if modified:
                    sm += 1
                else:
                    sn += 1

        snap_mod.append(sm)
        snap_new.append(sn)
        tui_no_snap.append(tns)
        non_tui.append(nt)

    fig, ax = plt.subplots(figsize=(10, 5.5))
    x = range(len(months))
    w = 0.65

    b1 = ax.bar(x, snap_mod, width=w, color="#27AE60",
                label="UI + existing snapshots updated", edgecolor="white")
    b2 = ax.bar(x, snap_new, width=w, bottom=snap_mod, color="#56CCF2",
                label="UI + new snapshots added", edgecolor="white")
    bottom2 = [a + b for a, b in zip(snap_mod, snap_new)]
    b3 = ax.bar(x, tui_no_snap, width=w, bottom=bottom2, color="#F2994A",
                label="UI bug, no snapshots touched", edgecolor="white")
    bottom3 = [a + b for a, b in zip(bottom2, tui_no_snap)]
    b4 = ax.bar(x, non_tui, width=w, bottom=bottom3, color="#BDBDBD",
                label="Non-UI bug", edgecolor="white")

    totals = [a + b + c + d for a, b, c, d in zip(snap_mod, snap_new, tui_no_snap, non_tui)]
    for i, t in enumerate(totals):
        if t > 0:
            ax.text(i, t + 0.2, str(t), ha="center", va="bottom", fontsize=9, fontweight="bold")

    adopt_idx = months.index("2026-01") if "2026-01" in months else None
    if adopt_idx is not None:
        ax.axvline(adopt_idx - 0.5, color="#333", linestyle="--", alpha=0.4, linewidth=1.5)

    ax.set_xticks(x)
    ax.set_xticklabels(months, rotation=30, ha="right")
    ax.set_ylabel("UX-regression bug-fix PRs")
    ax.set_title("UX Regressions by Month — Where Did They Land?\n"
                 "Most TUI bugs (orange) had no snapshot involvement")
    ax.legend(fontsize=9, loc="upper left")
    fig.tight_layout()
    fig.savefig(out / "deep_11_ux_regression_breakdown.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 12: UX-regression snapshot pattern — pie of all UX bugs
# ---------------------------------------------------------------------------

def fig_ux_snapshot_involvement(rows, full, out):
    """Donut: of all UX-regression bug-fixes, how many involved snapshots?"""
    all_ux = [r for r in rows if r["llm_pr_type"] == "bug-fix"
              and r["llm_bug_severity"] == "ux-regression"]

    tui_snap_mod = 0
    tui_snap_new = 0
    tui_no_snap = 0
    non_tui = 0

    for r in all_ux:
        tui = is_tui(r)
        has_snap = r["h_has_snapshot_changes"] == "true"
        if not tui:
            non_tui += 1
        elif not has_snap:
            tui_no_snap += 1
        else:
            added, modified = snap_breakdown(r["pr_number"], full)
            if modified:
                tui_snap_mod += 1
            else:
                tui_snap_new += 1

    labels = [
        f"TUI, existing\nsnapshots updated\n({tui_snap_mod})",
        f"TUI, new\nsnapshots added\n({tui_snap_new})",
        f"TUI, no snapshot\ninvolvement\n({tui_no_snap})",
        f"Non-TUI\n({non_tui})",
    ]
    sizes = [tui_snap_mod, tui_snap_new, tui_no_snap, non_tui]
    colors = ["#27AE60", "#56CCF2", "#F2994A", "#BDBDBD"]

    # Remove zero slices
    filtered = [(l, s, c) for l, s, c in zip(labels, sizes, colors) if s > 0]
    labels, sizes, colors = zip(*filtered)

    fig, ax = plt.subplots(figsize=(7, 5.5))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, colors=colors, autopct="%1.0f%%",
        startangle=90, pctdistance=0.75, textprops={"fontsize": 10},
    )
    for t in autotexts:
        t.set_fontweight("bold")

    centre = plt.Circle((0, 0), 0.50, fc="white")
    ax.add_artist(centre)
    ax.text(0, 0, f"{sum(sizes)}\nUX\nregressions",
            ha="center", va="center", fontsize=12, fontweight="bold")

    ax.set_title("UX-Regression Bug-Fixes: Snapshot Involvement\n"
                 "Only 23% touched snapshots at all")
    fig.tight_layout()
    fig.savefig(out / "deep_12_ux_snapshot_involvement.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Graph 16: Bug-fix domain breakdown — visual vs non-visual
# ---------------------------------------------------------------------------

def fig_bugfix_domain(rows, full, out):
    """Horizontal bar: bug-fixes by code domain with snapshot involvement."""
    bugfixes = [r for r in rows if r["llm_pr_type"] == "bug-fix"]

    def classify(r):
        pr = full.get(int(r["pr_number"]), {})
        files = [f["filename"] for f in pr.get("pr_files", [])]
        tui = is_tui(r)
        has_widget = any(kw in f for f in files
                         for kw in ("screen", "panel", "widget", "visualizer", "textual_app"))
        has_settings = any("setting" in f for f in files)
        has_api = any(kw in f for f in files
                      for kw in ("api_client", "auth", "login", "oauth"))
        has_config = any(kw in f for f in files
                         for kw in ("config", "condenser", "agent_store"))
        if not tui:
            if has_api:
                return "API / auth"
            elif has_config:
                return "Config / state mgmt"
            else:
                return "CLI core / other"
        else:
            if has_widget:
                return "TUI widgets & screens"
            elif has_settings:
                return "Settings UI logic"
            else:
                return "Other TUI"

    from collections import Counter
    domain_total = Counter()
    domain_snap = Counter()
    for r in bugfixes:
        d = classify(r)
        domain_total[d] += 1
        if r["h_has_snapshot_changes"] == "true":
            domain_snap[d] += 1

    # Order: visual first (descending), then non-visual
    visual_order = ["TUI widgets & screens", "Settings UI logic", "Other TUI"]
    non_visual_order = ["CLI core / other", "Config / state mgmt", "API / auth"]
    order = [d for d in visual_order + non_visual_order if domain_total[d] > 0]

    labels = order
    totals = [domain_total[d] for d in order]
    snaps = [domain_snap[d] for d in order]
    no_snaps = [t - s for t, s in zip(totals, snaps)]

    fig, ax = plt.subplots(figsize=(10, 5))
    y = range(len(labels))

    # Determine visual vs non-visual boundary
    vis_count = sum(1 for d in order if d in visual_order)

    bars_snap = ax.barh(y, snaps, height=0.55, color=C["snap"],
                        label="With snapshot involvement", edgecolor="white")
    bars_no = ax.barh(y, no_snaps, height=0.55, left=snaps, color="#F2994A",
                      label="No snapshot involvement", edgecolor="white")

    for i, (s, ns, t) in enumerate(zip(snaps, no_snaps, totals)):
        ax.text(t + 0.5, i, str(t), va="center", fontsize=11, fontweight="bold")

    # Add visual/non-visual grouping
    if vis_count < len(order):
        ax.axhline(vis_count - 0.5, color="#333", linestyle="--", alpha=0.3)
        ax.text(max(totals) * 0.85, vis_count - 0.7, "visual", fontsize=9,
                color="#666", ha="center", style="italic")
        ax.text(max(totals) * 0.85, vis_count - 0.3, "non-visual", fontsize=9,
                color="#666", ha="center", style="italic")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel("Number of bug-fix PRs")
    ax.set_title(f"Bug-Fixes by Code Domain (n={len(bugfixes)})")
    ax.invert_yaxis()
    ax.legend(fontsize=9, loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "deep_16_bugfix_domain.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 17: Bug severity × visual/non-visual
# ---------------------------------------------------------------------------

def fig_severity_by_domain(rows, full, out):
    """Grouped bar: severity split by visual vs non-visual."""
    bugfixes = [r for r in rows if r["llm_pr_type"] == "bug-fix"]

    sevs = ["critical", "ux-regression", "polish"]
    sev_labels = ["Critical", "UX regression", "Polish"]

    vis_counts = []
    nonvis_counts = []
    for sev in sevs:
        sev_bugs = [r for r in bugfixes if r["llm_bug_severity"] == sev]
        vis = sum(1 for r in sev_bugs if is_tui(r))
        nonvis = len(sev_bugs) - vis
        vis_counts.append(vis)
        nonvis_counts.append(nonvis)

    fig, ax = plt.subplots(figsize=(8, 5))
    x = range(len(sevs))
    w = 0.35
    ax.bar([i - w/2 for i in x], vis_counts, width=w, color=C["feature"],
           label="Visual (UI)", edgecolor="white")
    ax.bar([i + w/2 for i in x], nonvis_counts, width=w, color="#BDBDBD",
           label="Non-visual", edgecolor="white")

    for i, (v, nv) in enumerate(zip(vis_counts, nonvis_counts)):
        ax.text(i - w/2, v + 0.3, str(v), ha="center", fontsize=10, fontweight="bold")
        ax.text(i + w/2, nv + 0.3, str(nv), ha="center", fontsize=10, fontweight="bold")

    # Add percentage labels
    for i, (v, nv) in enumerate(zip(vis_counts, nonvis_counts)):
        total = v + nv
        if total > 0:
            ax.text(i + w/2, nv + 1.5,
                    f"{100*nv/total:.0f}%\nnon-vis", ha="center", fontsize=8, color="#666")

    ax.set_xticks(x)
    ax.set_xticklabels(sev_labels, fontsize=11)
    ax.set_ylabel("Bug-fix PRs")
    ax.set_title("Bug Severity by Domain")
    ax.legend(fontsize=10)
    ax.set_ylim(0, max(max(vis_counts), max(nonvis_counts)) * 1.4)
    fig.tight_layout()
    fig.savefig(out / "deep_17_severity_by_domain.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 13: Bug-fix coverage gap — the 85/12/4 split
# ---------------------------------------------------------------------------

def fig_bugfix_coverage_gap(rows, full, out):
    """Horizontal bar: 3 categories of bug-fix snapshot behavior."""
    all_bugs = [r for r in rows if r["llm_pr_type"] == "bug-fix"]
    uncovered, modified, new = 0, 0, 0

    for r in all_bugs:
        if r["h_has_snapshot_changes"] != "true":
            uncovered += 1
            continue
        pr = full.get(int(r["pr_number"]), {})
        files = [f for f in pr.get("pr_files", [])
                 if "snapshot" in f["filename"].lower() or f["filename"].endswith(".svg")]
        added = [f for f in files if f.get("status") == "added"]
        mod = [f for f in files if f.get("status") == "modified"]
        if added and not mod:
            new += 1
        elif mod:
            modified += 1
        else:
            uncovered += 1

    labels = [
        "No snapshots\n(uncovered before & after)",
        "Modified existing\n(behavior change acknowledged)",
        "Added new snapshots\n(coverage extended)",
    ]
    values = [uncovered, modified, new]
    colors = ["#EB5757", "#F2994A", "#27AE60"]

    fig, ax = plt.subplots(figsize=(10, 4))
    bars = ax.barh(range(len(labels)), values, color=colors, edgecolor="white", height=0.55)
    for bar, v, total in zip(bars, values, [len(all_bugs)] * 3):
        ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
                f"{v}  ({100 * v / total:.0f}%)", va="center", fontsize=12, fontweight="bold")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel("Number of bug-fix PRs")
    ax.set_title(f"Bug-Fix PRs: Snapshot Coverage Gap (n={len(all_bugs)})")
    ax.invert_yaxis()
    fig.tight_layout()
    fig.savefig(out / "deep_13_bugfix_coverage_gap.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 14: Snapshot guarding existing behavior — commit evidence
# ---------------------------------------------------------------------------

def fig_snapshot_guarding(rows, full, out):
    """Bar chart: snapshot-fix commits by PR type (feature, refactor, etc.)."""
    snap_prs = [r for r in rows
                if r["h_has_snapshot_changes"] == "true"
                and r["llm_pr_type"] not in ("version-bump", "dependency-bump")
                and int(r.get("pr_commit_count", 0)) >= 2]

    events_by_type = Counter()
    prs_by_type = defaultdict(set)

    for r in snap_prs:
        pr = full.get(int(r["pr_number"]), {})
        commits = pr.get("pr_commits", [])
        for i, c in enumerate(commits):
            msg = (c.get("message") or "").lower()
            if i > 0 and any(kw in msg for kw in SNAP_KEYWORDS):
                events_by_type[r["llm_pr_type"]] += 1
                prs_by_type[r["llm_pr_type"]].add(r["pr_number"])

    types = ["feature", "bug-fix", "refactor", "test"]
    event_vals = [events_by_type.get(t, 0) for t in types]
    pr_vals = [len(prs_by_type.get(t, set())) for t in types]
    type_colors = [C.get(t, "#888") for t in types]

    fig, ax = plt.subplots(figsize=(9, 5))
    x = range(len(types))
    bars = ax.bar(x, event_vals, color=type_colors, edgecolor="white", width=0.6)
    for bar, ev, pr_c in zip(bars, event_vals, pr_vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                f"{ev} commits\n({pr_c} PRs)", ha="center", va="bottom", fontsize=10)

    ax.set_xticks(x)
    ax.set_xticklabels([t.replace("-", "\n").title() for t in types], fontsize=11)
    ax.set_ylabel("Snapshot-fix commits (later in PR)")
    ax.set_title('"Code First, Fix Snapshots Later"')
    ax.set_ylim(0, max(event_vals) * 1.35)
    fig.tight_layout()
    fig.savefig(out / "deep_14_snapshot_guarding.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Graph 15: Covered vs uncovered bug accumulation over time
# ---------------------------------------------------------------------------

def fig_covered_vs_uncovered(rows, full, out):
    """Stacked bar: 3-way bug path coverage (covered / file-but-not-path / fully uncovered)."""
    rows_by_date = sorted(rows, key=lambda r: r.get("pr_created_at", ""))
    months = sorted(set(r.get("pr_created_at", "")[:7] for r in rows
                        if r.get("pr_created_at", "")[:7] >= "2026-01"))

    covered_files = set()
    for r in rows_by_date:
        if r["h_has_snapshot_changes"] == "true":
            pr = full.get(int(r["pr_number"]), {})
            covered_files |= set(
                f["filename"] for f in pr.get("pr_files", [])
                if f["filename"].endswith(".py")
                and "snapshot" not in f["filename"].lower())

    cat_a = []  # path covered (PR has snap changes)
    cat_b = []  # file covered, path not (PR has no snaps)
    cat_c = []  # fully uncovered

    for m in months:
        tui_bugs = [r for r in rows if r.get("pr_created_at", "")[:7] == m
                    and r["llm_pr_type"] == "bug-fix" and is_tui(r)]
        a, b, c = 0, 0, 0
        for r in tui_bugs:
            if r["h_has_snapshot_changes"] == "true":
                a += 1
            else:
                pr = full.get(int(r["pr_number"]), {})
                code_files = set(
                    f["filename"] for f in pr.get("pr_files", [])
                    if f["filename"].endswith(".py")
                    and "snapshot" not in f["filename"].lower()
                    and not f["filename"].endswith(".svg"))
                if code_files & covered_files:
                    b += 1
                else:
                    c += 1
        cat_a.append(a)
        cat_b.append(b)
        cat_c.append(c)

    fig, ax = plt.subplots(figsize=(10, 5.5))
    x = range(len(months))
    w = 0.6

    ax.bar(x, cat_a, width=w, color=C["snap"],
           label="Bug path snapshot-covered (PR updates snapshots)")
    ax.bar(x, cat_b, width=w, bottom=cat_a, color="#F2994A",
           label="File has coverage, but bug path uncovered")
    bottom2 = [a + b for a, b in zip(cat_a, cat_b)]
    ax.bar(x, cat_c, width=w, bottom=bottom2, color="#EB5757",
           label="Fully uncovered area")

    totals = [a + b + c for a, b, c in zip(cat_a, cat_b, cat_c)]
    for i, t in enumerate(totals):
        if t > 0:
            ax.text(i, t + 0.2, str(t), ha="center", va="bottom",
                    fontsize=10, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(months, rotation=30, ha="right")
    ax.set_ylabel("UI bug-fix PRs")
    ax.set_title("Path-Level Coverage of UI Bugs")
    ax.legend(fontsize=9, loc="upper right")
    fig.tight_layout()
    fig.savefig(out / "deep_15_covered_vs_uncovered.png", dpi=150)
    plt.close(fig)


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
    ("Bug rate with codebase growth context", fig_bug_rate_with_context),
    ("UX regression breakdown by month", fig_ux_regression_breakdown),
    ("UX regression snapshot involvement", fig_ux_snapshot_involvement),
    ("Bug-fix coverage gap", fig_bugfix_coverage_gap),
    ("Snapshot guarding existing behavior", fig_snapshot_guarding),
    ("Covered vs uncovered bug accumulation", fig_covered_vs_uncovered),
    ("Bug-fix domain breakdown", fig_bugfix_domain),
    ("Severity by domain", fig_severity_by_domain),
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
