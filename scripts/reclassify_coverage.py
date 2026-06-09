#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib>=3.9"]
# ///
"""
Reclassify snapshot coverage using empirical evidence from bug-fix PRs.

The previous analysis (analyze_indirect_coverage.py) classified bugs into:
  A  — "directly covered": bug-fix PR updated snapshot baselines
  B1 — "indirectly covered": a snapshot test exercises the flow (theoretical)
  B2 — "adjacent": module has tests but not for this behavior
  C  — "fully uncovered": no snapshot coverage at all

Problems with that classification:
  1. Category A conflates two cases: (a) the test specifically targets the buggy
     behavior vs (b) the test captures the bug's visual effect as part of a
     broader flow. Both update baselines, but the coverage relationship differs.
  2. Category B1 claims indirect coverage but the bug-fix PR did NOT update any
     snapshots — meaning the test wasn't actually sensitive to the bug. This is
     empirically indistinguishable from B2 (adjacent).

This script reclassifies using empirical evidence:
  - For each Category A PR, we examine WHICH snapshot tests were updated and
    whether those tests specifically target the buggy behavior (direct) or
    capture it as a side effect of a broader flow (indirect).
  - Category B1 is collapsed into uncovered (adjacent), since the fix PR not
    touching snapshots proves the test wasn't sensitive to the bug.

New classification:
  DIRECT   — snapshot test specifically targets the buggy behavior, and the
             fix PR updated that test's baseline.
  INDIRECT — snapshot test captures the bug's visual effect as part of a
             broader flow (the test wasn't designed for this behavior, but
             the fix PR still required updating its baseline).
  UNCOVERED — no snapshot test is sensitive to this bug (old B1 + B2 + C).

Usage:
    uv run scripts/reclassify_coverage.py

Requires:
    - data/categorized_prs.csv (from categorize_prs.py)
    - data/mined_prs.json (from mine_prs.py)
"""

import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# =============================================================================
# Category A reclassification map
#
# For each of the 12 bug-fix PRs that updated snapshot baselines, we examined:
#   1. Which snapshot test suites were updated (from the SVG file paths)
#   2. What the bug was (from PR title + code files changed)
#   3. Whether the updated test specifically targets the buggy behavior (direct)
#      or captures it as a visual side effect of a broader flow (indirect)
#
# "Direct" means the test suite name and purpose align with the bug — the test
# was designed to verify the behavior that broke.
#
# "Indirect" means the test captures a full page/flow, and the bug's visual
# effect happens to be visible in the snapshot even though the test wasn't
# designed to catch this specific behavior. The key evidence: the fix PR DID
# update the baseline, proving the test IS sensitive (unlike old B1).
# =============================================================================

CATEGORY_A_MAP = {
    # PR# → (classification, rationale)

    # --- DIRECT: test specifically targets the buggy behavior ---
    698: ("direct",
         "Auto-focus bug. TestAutoFocusPreservesCharacter is specifically designed "
         "to test auto-focus behavior — test name matches the bug exactly."),

    454: ("direct",
         "History panel close button + Escape. TestHistoryPanelFlow (all 6 phases) "
         "specifically tests the history panel, which is the component being fixed."),

    435: ("direct",
         "History panel + conversation switching. PR adds brand new "
         "TestHistoryPanelFlow tests — this is creating direct coverage."),

    391: ("direct",
         "Critic feedback text missing 'success' prediction. "
         "TestCriticFeedbackWidgetSnapshots specifically renders this widget."),

    363: ("direct",
         "CLI crashes when opening MCP panel. TestMCPSidePanelSnapshots "
         "(4 states) specifically renders the MCP panel component."),

    # --- INDIRECT: test captures the bug's visual effect in a broader flow ---
    504: ("indirect",
         "Collapsibles default to wrong state. Code change is in cli_settings.py "
         "(settings store), but the collapsed/expanded visual state shows up across "
         "10 snapshots in 5 unrelated test suites (ConfirmationMode, CreatingAgentPlan, "
         "EchoHelloWorld, HistoryPanel, MultilineMode) that render conversation events "
         "with collapsible sections."),

    497: ("indirect",
         "Agent name prefix shown for default agents. Code is in "
         "richlog_visualizer.py, but the name prefix change is visible in any test "
         "that renders conversation events — 8 snapshots across 4 test suites "
         "(ConfirmationMode, EchoHelloWorld, HistoryPanel, MultilineMode)."),

    439: ("indirect",
         "Token metrics don't reset on new conversation. Code is in textual_app.py "
         "and status_line.py. TestHistoryPanelFlow.phase3_new_conversation captures "
         "the state after creating a new conversation — the status line with token "
         "metrics is visible because the test captures the full app state, not because "
         "it's testing token metrics."),

    438: ("indirect",
         "Slash commands visible in vertical scroll. Code is in textual_app.py. "
         "TestHistoryPanelFlow phases 1-2 capture the full app state including the "
         "main conversation area where slash commands appear, even though the test "
         "is about the history panel."),

    687: ("indirect",
         "Tab navigation broken in settings modal. Code changes in settings tab "
         "components and settings_screen.py. TestInitialSetupFlow (4 phases) renders "
         "the settings page as part of the setup flow — the focus state change from "
         "Tab behavior is visible in the capture, but the test isn't specifically "
         "testing Tab navigation."),

    686: ("indirect",
         "Missing ESC/Ctrl+C/Ctrl+Q bindings on modals. Code changes in exit_modal.py, "
         "settings_screen.py, switch_conversation_modal.py. TestExitModalSnapshots + "
         "TestInitialSetupFlow (6 phases) render modals, but the visual change is in "
         "the key binding hints in the footer, not the modal behavior itself."),

    581: ("indirect",
         "Missing tab navigation hint in footers. Code changes in settings_screen.py "
         "and textual_app.py. TestInitialSetupFlow (4 phases) captures the full "
         "settings page including the footer — the hint is visible because the test "
         "renders the complete page, not because it's testing footer content."),
}


def is_tui(r):
    return r.get("llm_touches_tui", "").lower() == "true" or r.get("h_touches_tui", "") == "true"


def load_data(csv_path, json_path):
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    with open(json_path) as f:
        full = {p["pr_number"]: p for p in json.load(f)["pull_requests"]}
    return rows, full


def get_post_adoption_tui_bugfixes(rows):
    """Get all post-adoption TUI bug-fix PRs."""
    return [r for r in rows
            if r.get("pr_created_at", "") >= "2026-01"
            and r["llm_pr_type"] == "bug-fix"
            and is_tui(r)]


def classify_all(rows, full):
    """Classify all post-adoption TUI bug-fixes into direct, indirect, uncovered."""
    direct, indirect, uncovered = [], [], []

    for r in get_post_adoption_tui_bugfixes(rows):
        pr_num = int(r["pr_number"])
        pr = full.get(pr_num, {})

        if r["h_has_snapshot_changes"] == "true":
            # Category A: fix PR updated snapshots — check direct vs indirect
            if pr_num in CATEGORY_A_MAP:
                cls, rationale = CATEGORY_A_MAP[pr_num]
                entry = {"pr": pr_num, "title": r["pr_title"][:70], "rationale": rationale}
                if cls == "direct":
                    direct.append(entry)
                else:
                    indirect.append(entry)
            else:
                # Category A PR not in our map — flag for investigation
                print(f"  WARNING: Category A PR #{pr_num} not in CATEGORY_A_MAP")
                direct.append({"pr": pr_num, "title": r["pr_title"][:70],
                               "rationale": "UNMAPPED — defaulting to direct"})
        else:
            # Category B + C: fix PR did NOT update snapshots — uncovered
            uncovered.append({"pr": pr_num, "title": r["pr_title"][:70]})

    return direct, indirect, uncovered


def generate_graph(direct_count, indirect_count, uncovered_count, out_dir):
    """Generate the reclassified coverage graph."""
    fig, ax = plt.subplots(figsize=(10, 4.5))

    labels = [
        "Directly covered\n(test targets buggy behavior)",
        "Indirectly covered\n(bug visible in broader flow)",
        "Uncovered\n(no sensitive snapshot test)",
    ]
    values = [direct_count, indirect_count, uncovered_count]
    colors = ["#27AE60", "#56CCF2", "#F2994A"]

    bars = ax.barh(range(len(labels)), values, color=colors, height=0.55, edgecolor="white")
    total = sum(values)
    for bar, v in zip(bars, values):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                f"{v}  ({100 * v / total:.0f}%)", va="center", fontsize=11, fontweight="bold")

    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel("Post-adoption TUI bug-fix PRs")
    ax.set_title(f"Empirical Coverage Classification (n={total})\n"
                 "Based on whether bug-fix PRs updated snapshot baselines")
    ax.invert_yaxis()
    ax.set_xlim(0, max(values) + 4)
    fig.tight_layout()

    out_path = out_dir / "reclassified_coverage_v2.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"  Graph saved: {out_path}")


def main():
    p = argparse.ArgumentParser(description="Reclassify snapshot coverage with empirical evidence")
    p.add_argument("--input-csv", default="data/categorized_prs.csv")
    p.add_argument("--input-json", default="data/mined_prs.json")
    p.add_argument("--output-dir", default="graphs")
    args = p.parse_args()

    rows, full = load_data(args.input_csv, args.input_json)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    direct, indirect, uncovered = classify_all(rows, full)

    print(f"Direct coverage ({len(direct)}):")
    for e in direct:
        print(f"  #{e['pr']:>4} {e['title']}")
        print(f"        → {e['rationale']}")

    print(f"\nIndirect coverage ({len(indirect)}):")
    for e in indirect:
        print(f"  #{e['pr']:>4} {e['title']}")
        print(f"        → {e['rationale']}")

    print(f"\nUncovered ({len(uncovered)}):")
    for e in uncovered:
        print(f"  #{e['pr']:>4} {e['title']}")

    total = len(direct) + len(indirect) + len(uncovered)
    covered = len(direct) + len(indirect)
    print(f"\n{'='*60}")
    print(f"EMPIRICAL COVERAGE BREAKDOWN (n={total})")
    print(f"{'='*60}")
    print(f"  Direct:     {len(direct):3d} ({100*len(direct)/total:.0f}%)")
    print(f"  Indirect:   {len(indirect):3d} ({100*len(indirect)/total:.0f}%)")
    print(f"  Uncovered:  {len(uncovered):3d} ({100*len(uncovered)/total:.0f}%)")
    print(f"\n  Total covered (direct + indirect): {covered} ({100*covered/total:.0f}%)")
    print(f"  Total uncovered:                   {len(uncovered)} ({100*len(uncovered)/total:.0f}%)")
    print(f"\n  Previous classification:  12 direct, 4 indirect, 19 uncovered")
    print(f"  Revised classification:   {len(direct)} direct, {len(indirect)} indirect, {len(uncovered)} uncovered")

    generate_graph(len(direct), len(indirect), len(uncovered), out)


if __name__ == "__main__":
    main()
