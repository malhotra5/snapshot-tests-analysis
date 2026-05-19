#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib>=3.9"]
# ///
"""
Generate "Agent-Authored PRs Over Time" graph.

Shows monthly stacked bars of agent vs human PRs with a vertical line at
the snapshot adoption date (Jan 2026), establishing that agent PR volume
was relatively stable before and after snapshot introduction.

Usage:
    python scripts/generate_agent_prs_timeline.py
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# Styling (matches existing graphs)
# ---------------------------------------------------------------------------

COLORS = {
    "agent": "#5B8DEF",
    "human": "#F2994A",
    "bot": "#BDBDBD",
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
        "axes.titlesize": 14,
        "axes.titleweight": "bold",
    })


def load(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def month_of(r):
    return r.get("pr_created_at", "")[:7]


def generate(rows, out):
    """Stacked bar chart: agent vs human vs bot PRs per month, with snapshot adoption line."""
    months = sorted(set(month_of(r) for r in rows if month_of(r)))

    counts = defaultdict(lambda: defaultdict(int))
    for r in rows:
        m = month_of(r)
        if m:
            auth = r.get("authorship", "human")
            # Combine bot + agent as "agent-assisted" vs "human"
            if auth in ("agent", "bot"):
                counts[m]["agent"] += 1
            else:
                counts[m]["human"] += 1

    agent_vals = [counts[m]["agent"] for m in months]
    human_vals = [counts[m]["human"] for m in months]
    totals = [a + h for a, h in zip(agent_vals, human_vals)]

    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(months))
    w = 0.6

    bars_agent = ax.bar(x, agent_vals, width=w, color=COLORS["agent"],
                        label="Agent-authored", edgecolor="white")
    bars_human = ax.bar(x, human_vals, width=w, bottom=agent_vals,
                        color=COLORS["human"], label="Human-authored", edgecolor="white")

    # Annotate totals above each bar
    for i, t in enumerate(totals):
        ax.text(i, t + 0.8, str(t), ha="center", va="bottom",
                fontsize=10, fontweight="bold")

    # Annotate agent counts inside bars
    for i, a in enumerate(agent_vals):
        if a > 0:
            ax.text(i, a / 2, str(a), ha="center", va="center",
                    fontsize=9, color="white", fontweight="bold")

    # Annotate human counts inside bars
    for i, (a, h) in enumerate(zip(agent_vals, human_vals)):
        if h > 0:
            ax.text(i, a + h / 2, str(h), ha="center", va="center",
                    fontsize=9, color="white", fontweight="bold")

    # Vertical line for snapshot adoption
    adopt_idx = months.index("2026-01") if "2026-01" in months else None
    if adopt_idx is not None:
        ax.axvline(adopt_idx - 0.5, color="#EB5757", linestyle="--",
                   alpha=0.7, linewidth=1.5)
        ax.text(adopt_idx - 0.4, max(totals) * 0.95, "snapshot\nadoption",
                fontsize=9, color="#EB5757", va="top")

    # Agent % line on secondary axis
    ax2 = ax.twinx()
    pcts = [100 * a / max(t, 1) for a, t in zip(agent_vals, totals)]
    ax2.plot(x, pcts, color="#27AE60", marker="o", linewidth=2,
             label="% agent-authored", zorder=5)
    ax2.set_ylabel("% agent-authored", color="#27AE60")
    ax2.tick_params(axis="y", labelcolor="#27AE60")
    ax2.set_ylim(0, 110)
    ax2.legend(loc="upper right")

    ax.set_xticks(x)
    ax.set_xticklabels(months, rotation=30, ha="right")
    ax.set_ylabel("Number of PRs")
    ax.set_title("Agent-Authored PRs Over Time")
    ax.legend(loc="upper left")
    ax.set_ylim(0, max(totals) * 1.2)

    fig.tight_layout()
    dest = out / "agent_prs_over_time.png"
    fig.savefig(dest, dpi=150)
    plt.close(fig)
    print(f"  Saved → {dest}")
    return dest


def main():
    p = argparse.ArgumentParser(description="Generate agent PRs timeline graph")
    p.add_argument("--input", default="data/categorized_prs.csv")
    p.add_argument("--output-dir", default="graphs")
    args = p.parse_args()

    setup_style()
    rows = load(args.input)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(rows)} PRs")
    generate(rows, out)
    print("Done!")


if __name__ == "__main__":
    main()
