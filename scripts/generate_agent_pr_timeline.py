#!/usr/bin/env python3
"""Generate a stacked bar chart of PRs by authorship over time,
with snapshot-touching PRs overlaid to show adoption context."""

import csv
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from collections import defaultdict, Counter
from pathlib import Path

DATA_DIR = Path("data")
OUT_DIR = Path("graphs")

COLORS = {
    "agent": "#3498db",
    "human": "#2ecc71",
    "bot": "#95a5a6",
}

SNAPSHOT_ADOPTION = "2026-01"  # first month with snapshot PRs


def main():
    with open(DATA_DIR / "categorized_prs.csv") as f:
        rows = list(csv.DictReader(f))

    monthly_auth = defaultdict(lambda: Counter())
    monthly_snap = defaultdict(int)

    for r in rows:
        month = r.get("pr_created_at", "")[:7]
        auth = r.get("h_authorship", "unknown")
        monthly_auth[month][auth] += 1
        if r.get("h_has_snapshot_changes", "") == "true":
            monthly_snap[month] += 1

    months = sorted(monthly_auth.keys())
    x = np.arange(len(months))

    agent = [monthly_auth[m]["agent"] for m in months]
    human = [monthly_auth[m]["human"] for m in months]
    bot = [monthly_auth[m]["bot"] for m in months]
    snap = [monthly_snap.get(m, 0) for m in months]

    # --- Graph ---
    fig, ax = plt.subplots(figsize=(12, 5.5))

    bar_width = 0.6
    bars_bot = ax.bar(x, bot, bar_width, label="Bot (automated)", color=COLORS["bot"])
    bars_human = ax.bar(x, human, bar_width, bottom=bot, label="Human", color=COLORS["human"])
    bars_agent = ax.bar(x, agent, bar_width, bottom=[b + h for b, h in zip(bot, human)],
                        label="Agent-authored", color=COLORS["agent"])

    # Snapshot adoption line
    ax2 = ax.twinx()
    snap_line = ax2.plot(x, snap, color="#e74c3c", marker="o", linewidth=2.5,
                         markersize=7, label="PRs touching snapshots", zorder=5)
    ax2.set_ylabel("PRs touching snapshot files", color="#e74c3c", fontsize=11)
    ax2.tick_params(axis="y", labelcolor="#e74c3c")
    ax2.set_ylim(0, max(snap) * 2.5 if max(snap) > 0 else 10)

    # Adoption marker
    adoption_idx = months.index(SNAPSHOT_ADOPTION) if SNAPSHOT_ADOPTION in months else None
    if adoption_idx is not None:
        ax.axvline(x=adoption_idx - 0.5, color="#e74c3c", linestyle="--",
                   linewidth=1.5, alpha=0.7)
        ax.text(adoption_idx - 0.4, max(a + h + b for a, h, b in zip(agent, human, bot)) * 0.95,
                "snapshot\nadoption", color="#e74c3c", fontsize=9, ha="left", va="top",
                fontstyle="italic")

    # Labels
    month_labels = [m[2:] for m in months]  # "2025-09" → "25-09"
    ax.set_xticks(x)
    ax.set_xticklabels(month_labels, fontsize=10)
    ax.set_xlabel("Month", fontsize=11)
    ax.set_ylabel("Number of PRs", fontsize=11)
    ax.set_title("PRs by Authorship Over Time", fontsize=14, fontweight="bold")

    # Combined legend
    handles1, labels1 = ax.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(handles1 + handles2, labels1 + labels2, loc="upper left", fontsize=9)

    ax.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)

    fig.tight_layout()
    out_path = OUT_DIR / "deep_20_agent_prs_over_time.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
