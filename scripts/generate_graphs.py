#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib>=3.9"]
# ///
"""
Generate visual graphs from categorized PR data.

Usage (via uv):
    uv run scripts/generate_graphs.py --input data/categorized_prs.csv --output-dir graphs/
"""

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

COLORS = {
    "agent": "#5B8DEF",
    "human": "#F2994A",
    "bot": "#BDBDBD",
    "mixed": "#9B59B6",
    # PR types
    "feature": "#27AE60",
    "bug-fix": "#EB5757",
    "refactor": "#9B59B6",
    "ci": "#56CCF2",
    "test": "#F2C94C",
    "docs": "#BB6BD9",
    "version-bump": "#BDBDBD",
    "dependency-bump": "#828282",
    "other": "#E0E0E0",
    # Severity
    "critical": "#EB5757",
    "ux-regression": "#F2994A",
    "polish": "#F2C94C",
    "not-applicable": "#BDBDBD",
    # Periods
    "before": "#BDBDBD",
    "after": "#5B8DEF",
    # Snapshot
    "snapshot": "#27AE60",
    "no-snapshot": "#E0E0E0",
}

SEVERITY_ORDER = ["critical", "ux-regression", "polish", "not-applicable"]
TYPE_ORDER = ["feature", "bug-fix", "refactor", "ci", "test", "docs",
              "version-bump", "dependency-bump", "other"]


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
        "axes.titlesize": 14,
        "axes.titleweight": "bold",
        "figure.titlesize": 16,
        "figure.titleweight": "bold",
    })


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def is_tui(r):
    return (r.get("llm_touches_tui", "").lower() == "true"
            or r.get("h_touches_tui", "") == "true")


def month_of(r):
    return r.get("pr_created_at", "")[:7]


# ---------------------------------------------------------------------------
# Individual graph functions
# ---------------------------------------------------------------------------

def fig_authorship_pie(rows, out):
    """Pie chart of PR authorship."""
    counts = Counter(r["authorship"] for r in rows)
    labels = ["agent", "human", "bot"]
    sizes = [counts.get(l, 0) for l in labels]
    colors = [COLORS[l] for l in labels]

    fig, ax = plt.subplots(figsize=(6, 5))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=[f"{l}\n({s})" for l, s in zip(labels, sizes)],
        colors=colors, autopct="%1.0f%%", startangle=90,
        textprops={"fontsize": 12},
    )
    for t in autotexts:
        t.set_fontweight("bold")
    ax.set_title("PR Authorship Breakdown")
    fig.tight_layout()
    fig.savefig(out / "1_authorship_pie.png", dpi=150)
    plt.close(fig)


def fig_pr_types_by_authorship(rows, out):
    """Grouped bar chart: PR type distribution for agent vs human."""
    fig, ax = plt.subplots(figsize=(10, 5.5))

    for auth in ["agent", "human"]:
        subset = [r for r in rows if r["authorship"] == auth]
        types = Counter(r["llm_pr_type"] for r in subset)
        total = len(subset)
        pcts = [100 * types.get(t, 0) / total for t in TYPE_ORDER]
        x_offset = -0.18 if auth == "agent" else 0.18
        bars = ax.bar(
            [i + x_offset for i in range(len(TYPE_ORDER))],
            pcts, width=0.35, label=f"{auth} (n={total})",
            color=COLORS[auth], edgecolor="white", linewidth=0.5,
        )
        for bar, pct in zip(bars, pcts):
            if pct > 2:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                        f"{pct:.0f}%", ha="center", va="bottom", fontsize=8)

    ax.set_xticks(range(len(TYPE_ORDER)))
    ax.set_xticklabels([t.replace("-", "\n") for t in TYPE_ORDER], fontsize=9)
    ax.set_ylabel("% of PRs")
    ax.set_title("PR Type Distribution: Agent vs Human")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "2_pr_types_by_authorship.png", dpi=150)
    plt.close(fig)


def fig_snapshot_adoption_timeline(rows, out):
    """Stacked area chart showing snapshot adoption over time."""
    months = sorted(set(month_of(r) for r in rows if month_of(r)))

    total_by_month = []
    snap_by_month = []
    agent_snap_by_month = []

    for m in months:
        month_prs = [r for r in rows if month_of(r) == m]
        snap = [r for r in month_prs if r["h_has_snapshot_changes"] == "true"]
        agent_snap = [r for r in snap if r["authorship"] == "agent"]
        total_by_month.append(len(month_prs))
        snap_by_month.append(len(snap))
        agent_snap_by_month.append(len(agent_snap))

    fig, ax1 = plt.subplots(figsize=(10, 5))

    x = range(len(months))
    ax1.bar(x, total_by_month, color="#E0E0E0", label="Total PRs", edgecolor="white")
    ax1.bar(x, snap_by_month, color=COLORS["snapshot"], label="PRs with snapshot changes",
            edgecolor="white")
    ax1.bar(x, agent_snap_by_month, color="#1B7A3D", label="Agent PRs with snapshots",
            edgecolor="white")

    ax1.set_xticks(x)
    ax1.set_xticklabels(months, rotation=30, ha="right")
    ax1.set_ylabel("Number of PRs")
    ax1.set_title("Snapshot Test Adoption Over Time")
    ax1.legend(loc="upper left")

    # Add percentage line on second axis
    ax2 = ax1.twinx()
    pcts = [100 * s / max(t, 1) for s, t in zip(snap_by_month, total_by_month)]
    ax2.plot(x, pcts, color="#EB5757", marker="o", linewidth=2, label="% with snapshots")
    ax2.set_ylabel("% of PRs with snapshot changes", color="#EB5757")
    ax2.tick_params(axis="y", labelcolor="#EB5757")
    ax2.set_ylim(0, max(pcts) * 1.4)
    ax2.legend(loc="upper right")

    # Vertical line for adoption point
    adopt_idx = months.index("2026-01") if "2026-01" in months else None
    if adopt_idx is not None:
        ax1.axvline(adopt_idx - 0.5, color="#EB5757", linestyle="--", alpha=0.5, linewidth=1.5)
        ax1.text(adopt_idx - 0.4, max(total_by_month) * 0.95, "snapshot\nadoption",
                 fontsize=9, color="#EB5757", va="top")

    fig.tight_layout()
    fig.savefig(out / "3_snapshot_adoption_timeline.png", dpi=150)
    plt.close(fig)


def fig_bug_severity_before_after(rows, out):
    """Side-by-side bar chart: bug severity before vs after snapshot adoption."""
    before_agent = [r for r in rows if month_of(r) < "2026-01" and r["authorship"] == "agent"]
    after_agent = [r for r in rows if month_of(r) >= "2026-01" and r["authorship"] == "agent"]

    def severity_pcts(prs):
        bugs = [r for r in prs if r["llm_pr_type"] == "bug-fix"]
        total = len(prs)
        sevs = Counter(r["llm_bug_severity"] for r in bugs)
        return {s: 100 * sevs.get(s, 0) / max(total, 1) for s in SEVERITY_ORDER}

    before_pcts = severity_pcts(before_agent)
    after_pcts = severity_pcts(after_agent)

    labels_display = ["Critical", "UX\nRegression", "Polish"]
    sev_keys = ["critical", "ux-regression", "polish"]

    fig, ax = plt.subplots(figsize=(8, 5))
    x = range(len(sev_keys))
    w = 0.35

    bars_b = ax.bar([i - w/2 for i in x],
                    [before_pcts[s] for s in sev_keys],
                    width=w, label=f"Before snapshots (n={len(before_agent)})",
                    color=COLORS["before"], edgecolor="white")
    bars_a = ax.bar([i + w/2 for i in x],
                    [after_pcts[s] for s in sev_keys],
                    width=w, label=f"After snapshots (n={len(after_agent)})",
                    color=COLORS["after"], edgecolor="white")

    for bars in [bars_b, bars_a]:
        for bar in bars:
            h = bar.get_height()
            if h > 0.5:
                ax.text(bar.get_x() + bar.get_width() / 2, h + 0.3,
                        f"{h:.1f}%", ha="center", va="bottom", fontsize=10, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(labels_display, fontsize=11)
    ax.set_ylabel("% of agent PRs")
    ax.set_title("Agent Bug Severity: Before vs After Snapshot Adoption")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "4_bug_severity_before_after.png", dpi=150)
    plt.close(fig)


def fig_critical_bug_rate_timeline(rows, out):
    """Line chart: critical bug rate per month for agent PRs."""
    months = sorted(set(month_of(r) for r in rows if month_of(r)))

    crit_rates = []
    ux_rates = []
    for m in months:
        agent = [r for r in rows if month_of(r) == m and r["authorship"] == "agent"]
        crit = [r for r in agent if r["llm_pr_type"] == "bug-fix" and r["llm_bug_severity"] == "critical"]
        ux = [r for r in agent if r["llm_pr_type"] == "bug-fix" and r["llm_bug_severity"] == "ux-regression"]
        crit_rates.append(100 * len(crit) / max(len(agent), 1))
        ux_rates.append(100 * len(ux) / max(len(agent), 1))

    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(months))

    ax.plot(x, crit_rates, color=COLORS["critical"], marker="s", linewidth=2.5,
            markersize=8, label="Critical bugs", zorder=3)
    ax.plot(x, ux_rates, color=COLORS["ux-regression"], marker="o", linewidth=2.5,
            markersize=8, label="UX regressions", zorder=3)

    ax.fill_between(x, crit_rates, alpha=0.1, color=COLORS["critical"])
    ax.fill_between(x, ux_rates, alpha=0.1, color=COLORS["ux-regression"])

    adopt_idx = months.index("2026-01") if "2026-01" in months else None
    if adopt_idx is not None:
        ax.axvline(adopt_idx - 0.5, color="#333", linestyle="--", alpha=0.4, linewidth=1.5)
        ax.text(adopt_idx - 0.4, max(max(crit_rates), max(ux_rates)) * 0.95,
                "snapshot\nadoption", fontsize=9, color="#333", va="top")

    ax.set_xticks(x)
    ax.set_xticklabels(months, rotation=30, ha="right")
    ax.set_ylabel("% of agent PRs")
    ax.set_title("Agent Bug Rate Over Time (Critical vs UX Regression)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "5_bug_rate_timeline.png", dpi=150)
    plt.close(fig)


def fig_bug_to_feature_ratio(rows, out):
    """Bar chart: monthly bug-to-feature ratio for agent PRs."""
    months = sorted(set(month_of(r) for r in rows if month_of(r)))

    ratios = []
    feat_counts = []
    bug_counts = []
    for m in months:
        agent = [r for r in rows if month_of(r) == m and r["authorship"] == "agent"]
        feats = len([r for r in agent if r["llm_pr_type"] == "feature"])
        bugs = len([r for r in agent if r["llm_pr_type"] == "bug-fix"])
        ratios.append(bugs / max(feats, 1))
        feat_counts.append(feats)
        bug_counts.append(bugs)

    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(months))

    bar_colors = [COLORS["before"] if m < "2026-01" else COLORS["after"] for m in months]
    bars = ax.bar(x, ratios, color=bar_colors, edgecolor="white", linewidth=0.5)

    for bar, ratio, fc, bc in zip(bars, ratios, feat_counts, bug_counts):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                f"{bc}b/{fc}f", ha="center", va="bottom", fontsize=8, color="#555")

    ax.axhline(1.0, color="#EB5757", linestyle=":", alpha=0.5, linewidth=1)
    ax.text(len(months) - 0.5, 1.03, "1:1 ratio", fontsize=8, color="#EB5757",
            ha="right", va="bottom")

    adopt_idx = months.index("2026-01") if "2026-01" in months else None
    if adopt_idx is not None:
        ax.axvline(adopt_idx - 0.5, color="#333", linestyle="--", alpha=0.4, linewidth=1.5)

    ax.set_xticks(x)
    ax.set_xticklabels(months, rotation=30, ha="right")
    ax.set_ylabel("Bug-fix PRs / Feature PRs")
    ax.set_title("Agent Bug-to-Feature Ratio by Month")

    from matplotlib.patches import Patch
    ax.legend(handles=[
        Patch(facecolor=COLORS["before"], label="Before snapshots"),
        Patch(facecolor=COLORS["after"], label="After snapshots"),
    ])

    fig.tight_layout()
    fig.savefig(out / "6_bug_to_feature_ratio.png", dpi=150)
    plt.close(fig)


def fig_tui_bug_snapshot_involvement(rows, out):
    """Horizontal stacked bar: TUI bugs with vs without snapshot changes."""
    bugs = [r for r in rows if r["llm_pr_type"] == "bug-fix" and is_tui(r)]

    categories = {
        "Agent TUI bugs": [r for r in bugs if r["authorship"] == "agent"],
        "Human TUI bugs": [r for r in bugs if r["authorship"] == "human"],
    }

    fig, ax = plt.subplots(figsize=(9, 3.5))
    y_pos = range(len(categories))
    labels = list(categories.keys())

    with_snap = [len([r for r in v if r["h_has_snapshot_changes"] == "true"])
                 for v in categories.values()]
    without_snap = [len([r for r in v if r["h_has_snapshot_changes"] != "true"])
                    for v in categories.values()]

    bars1 = ax.barh(y_pos, with_snap, color=COLORS["snapshot"],
                    label="With snapshot changes", edgecolor="white")
    bars2 = ax.barh(y_pos, without_snap, left=with_snap, color=COLORS["no-snapshot"],
                    label="Without snapshot changes", edgecolor="white")

    for i, (ws, wos) in enumerate(zip(with_snap, without_snap)):
        total = ws + wos
        if ws > 0:
            ax.text(ws / 2, i, str(ws), ha="center", va="center", fontweight="bold", fontsize=11)
        if wos > 0:
            ax.text(ws + wos / 2, i, str(wos), ha="center", va="center", fontsize=11)
        ax.text(ws + wos + 0.5, i, f"(n={total})", ha="left", va="center",
                fontsize=9, color="#555")

    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=11)
    ax.set_xlabel("Number of TUI bug-fix PRs")
    ax.set_title("TUI Bug Fixes: Snapshot Involvement")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "7_tui_bug_snapshot_involvement.png", dpi=150)
    plt.close(fig)


def fig_snapshot_bugfix_severity(rows, out):
    """Donut chart: severity of bug-fix PRs that include snapshot changes."""
    snap_bugs = [r for r in rows
                 if r["llm_pr_type"] == "bug-fix" and r["h_has_snapshot_changes"] == "true"]

    sevs = Counter(r["llm_bug_severity"] for r in snap_bugs)
    labels = [s for s in SEVERITY_ORDER if sevs.get(s, 0) > 0]
    sizes = [sevs[s] for s in labels]
    colors = [COLORS.get(s, "#CCC") for s in labels]

    fig, ax = plt.subplots(figsize=(6, 5))
    wedges, texts, autotexts = ax.pie(
        sizes,
        labels=[f"{l.replace('-', ' ').title()}\n({s})" for l, s in zip(labels, sizes)],
        colors=colors, autopct="%1.0f%%", startangle=90,
        pctdistance=0.75, textprops={"fontsize": 11},
    )
    for t in autotexts:
        t.set_fontweight("bold")

    # Donut hole
    centre = plt.Circle((0, 0), 0.50, fc="white")
    ax.add_artist(centre)
    ax.text(0, 0, f"{len(snap_bugs)}\nbug fixes\nw/ snapshots",
            ha="center", va="center", fontsize=11, fontweight="bold")

    ax.set_title("Severity of Bug Fixes That Include\nSnapshot Changes")
    fig.tight_layout()
    fig.savefig(out / "8_snapshot_bugfix_severity.png", dpi=150)
    plt.close(fig)


def fig_merge_time_comparison(rows, out):
    """Box plot: merge times for agent vs human PRs."""
    data = {}
    for auth in ["agent", "human"]:
        times = []
        for r in rows:
            if r["authorship"] == auth and r.get("hours_to_merge", ""):
                try:
                    h = float(r["hours_to_merge"])
                    if h < 500:  # exclude extreme outliers
                        times.append(h)
                except (ValueError, TypeError):
                    pass
        data[auth] = times

    fig, ax = plt.subplots(figsize=(7, 5))
    bp = ax.boxplot(
        [data["agent"], data["human"]],
        tick_labels=[f"Agent\n(n={len(data['agent'])})", f"Human\n(n={len(data['human'])})"],
        patch_artist=True, widths=0.5,
        medianprops={"color": "black", "linewidth": 2},
    )
    bp["boxes"][0].set_facecolor(COLORS["agent"])
    bp["boxes"][1].set_facecolor(COLORS["human"])
    for box in bp["boxes"]:
        box.set_alpha(0.7)

    ax.set_ylabel("Hours to merge")
    ax.set_title("Time to Merge: Agent vs Human PRs")
    fig.tight_layout()
    fig.savefig(out / "9_merge_time_comparison.png", dpi=150)
    plt.close(fig)


def fig_summary_dashboard(rows, out):
    """Big-number summary dashboard."""
    total = len(rows)
    agent = len([r for r in rows if r["authorship"] == "agent"])
    snap = len([r for r in rows if r["h_has_snapshot_changes"] == "true"])

    before_agent = [r for r in rows if month_of(r) < "2026-01" and r["authorship"] == "agent"]
    after_agent = [r for r in rows if month_of(r) >= "2026-01" and r["authorship"] == "agent"]
    before_crit = len([r for r in before_agent
                       if r["llm_pr_type"] == "bug-fix" and r["llm_bug_severity"] == "critical"])
    after_crit = len([r for r in after_agent
                      if r["llm_pr_type"] == "bug-fix" and r["llm_bug_severity"] == "critical"])

    crit_before_pct = 100 * before_crit / max(len(before_agent), 1)
    crit_after_pct = 100 * after_crit / max(len(after_agent), 1)

    metrics = [
        (str(total), "Total PRs", "#333"),
        (f"{100*agent/total:.0f}%", "Agent-authored", COLORS["agent"]),
        (str(snap), "PRs with\nsnapshot changes", COLORS["snapshot"]),
        (f"{crit_before_pct:.1f}% → {crit_after_pct:.1f}%", "Critical bug rate\n(before → after)", COLORS["critical"]),
    ]

    fig, axes = plt.subplots(1, 4, figsize=(14, 3))
    for ax, (value, label, color) in zip(axes, metrics):
        ax.text(0.5, 0.6, value, ha="center", va="center",
                fontsize=28, fontweight="bold", color=color,
                transform=ax.transAxes)
        ax.text(0.5, 0.15, label, ha="center", va="center",
                fontsize=11, color="#555", transform=ax.transAxes)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")

    fig.suptitle("Snapshot Tests Impact — Key Metrics", fontsize=16, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(out / "0_summary_dashboard.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ALL_FIGURES = [
    ("Summary dashboard", fig_summary_dashboard),
    ("Authorship pie", fig_authorship_pie),
    ("PR types by authorship", fig_pr_types_by_authorship),
    ("Snapshot adoption timeline", fig_snapshot_adoption_timeline),
    ("Bug severity before/after", fig_bug_severity_before_after),
    ("Bug rate timeline", fig_critical_bug_rate_timeline),
    ("Bug-to-feature ratio", fig_bug_to_feature_ratio),
    ("TUI bug snapshot involvement", fig_tui_bug_snapshot_involvement),
    ("Snapshot bugfix severity", fig_snapshot_bugfix_severity),
    ("Merge time comparison", fig_merge_time_comparison),
]


def main():
    p = argparse.ArgumentParser(description="Generate graphs from categorized PRs")
    p.add_argument("--input", default="data/categorized_prs.csv",
                   help="Path to categorized CSV")
    p.add_argument("--output-dir", default="graphs",
                   help="Directory for output PNGs")
    args = p.parse_args()

    setup_style()
    rows = load(args.input)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(rows)} PRs from {args.input}")
    print(f"Generating {len(ALL_FIGURES)} graphs → {out}/\n")

    for name, func in ALL_FIGURES:
        print(f"  {name}...", end=" ", flush=True)
        func(rows, out)
        print("✓")

    print(f"\nDone! {len(ALL_FIGURES)} graphs saved to {out}/")


if __name__ == "__main__":
    main()
