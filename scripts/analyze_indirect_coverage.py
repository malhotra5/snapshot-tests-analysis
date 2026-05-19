#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib>=3.9"]
# ///
"""
Analyze indirect snapshot test coverage.

Snapshot tests don't just test visual rendering — they implicitly test the full
stack behind what's rendered. This script maps each snapshot test suite to the
functional behaviors it exercises, then reclassifies bug-fixes that we previously
called "uncovered" into three refined categories:

  B1 — Indirectly covered: a snapshot test exercises this functional flow, so a
       regression in this behavior SHOULD produce a different visual output.
  B2 — Adjacent: the file has snapshot tests, but no test exercises the specific
       behavior that broke.
  B3 — Unrelated: the file appears in snapshot PRs coincidentally.

The functional coverage map is derived from reading the actual test source code
in the openhands-cli repository. Each test suite is manually mapped to:
  - The user-level flow it exercises (e.g., "type message → confirm → see result")
  - The code modules it implicitly touches (e.g., settings persistence, conversation
    loading, event rendering)
  - The specific behaviors it would catch regressions in

Usage:
    uv run scripts/analyze_indirect_coverage.py

Requires:
    - data/categorized_prs.csv (from categorize_prs.py)
    - data/mined_prs.json (from mine_prs.py)

Methodology notes:
    The coverage map was built by reading every snapshot test file in
    openhands-cli/tests/snapshots/ (20 test files + conftest.py + helpers.py).
    For each test class, we identified:
    1. What fixtures it uses (mock_llm_setup, first_time_user_setup, etc.)
    2. What user actions it simulates (typing, pressing enter, navigating tabs)
    3. What code modules those actions must exercise to produce the rendered output
    4. What regressions would be visible in the snapshot (vs invisible)
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# =============================================================================
# Functional coverage map
#
# Built by reading every test file in openhands-cli/tests/snapshots/
# Each entry maps a test suite to the functional domains it exercises.
#
# Source files examined (openhands-cli @ HEAD as of 2026-05-18):
#   tests/snapshots/e2e/conftest.py         — fixtures: mock_llm_setup,
#       first_time_user_setup, mock_llm_with_trajectory, mock_llm_with_critic
#   tests/snapshots/e2e/helpers.py          — wait_for_app_ready, type_text, etc.
#   tests/snapshots/e2e/test_app_initial_state.py
#   tests/snapshots/e2e/test_app_with_typed_input.py
#   tests/snapshots/e2e/test_auto_focus.py
#   tests/snapshots/e2e/test_confirmation_mode.py
#   tests/snapshots/e2e/test_creating_agent_plan.py
#   tests/snapshots/e2e/test_echo_hello_world.py
#   tests/snapshots/e2e/test_history_panel.py
#   tests/snapshots/e2e/test_initial_setup_flow.py
#   tests/snapshots/e2e/test_iterative_refinement_case_a.py
#   tests/snapshots/e2e/test_iterative_refinement_case_b.py
#   tests/snapshots/e2e/test_multiline_mode_submit.py
#   tests/snapshots/e2e/test_select_all.py
#   tests/snapshots/e2e/test_skills_command.py
#   tests/snapshots/test_app_snapshots.py   — exit modal, MCP panel, settings tabs
#   tests/snapshots/test_autocomplete_dropdown_snapshots.py
#   tests/snapshots/test_critic_feedback_snapshots.py
#   tests/snapshots/test_critic_settings_tab_snapshots.py
#   tests/snapshots/test_history_search_snapshots.py
#   tests/snapshots/test_visualizer_snapshots.py
# =============================================================================

# Each test suite maps to a set of "functional behaviors" it exercises.
# A functional behavior is a string like "settings_persistence" or
# "conversation_loading" that can be matched against bug-fix descriptions.
COVERAGE_MAP = {
    # === E2E tests (full app, mock LLM, trajectory replay) ===
    "TestAppInitialState": {
        "description": "App launch → splash screen rendering",
        "flow": "Launch app with mock LLM → wait for ready → snapshot",
        "behaviors": {
            "app_initialization",      # OpenHandsApp.__init__, compose, on_mount
            "widget_rendering",        # Input widget, footer, splash screen
            "theme_application",       # OPENHANDS_THEME applied correctly
        },
        "modules_exercised": [
            "textual_app.py",          # App creation and compose
            "richlog_visualizer.py",   # ConversationVisualizer init
        ],
    },
    "TestAppWithTypedInput": {
        "description": "Type text in input field → verify rendering",
        "flow": "Launch → type 'echo hello world' → snapshot (before submit)",
        "behaviors": {
            "app_initialization",
            "input_widget_rendering",  # Text appears in input field
            "widget_rendering",
        },
        "modules_exercised": [
            "textual_app.py",
        ],
    },
    "TestAutoFocusPreservesCharacter": {
        "description": "Tab away from input → type char → verify auto-focus + char preserved",
        "flow": "Launch → Tab (defocus) → press 'h' → snapshot shows 'h' in input",
        "behaviors": {
            "input_focus_management",  # Auto-focus behavior
            "input_widget_rendering",
        },
        "modules_exercised": [
            "textual_app.py",
        ],
    },
    "TestConfirmationMode": {
        "description": "Full confirmation flow: type → confirm panel → select policy → repeat",
        "flow": "6 phases: init → type+confirm → Auto LOW/MED → HIGH risk → confirm → LOW auto",
        "behaviors": {
            "app_initialization",
            "input_widget_rendering",
            "confirmation_panel_rendering",  # Confirmation panel UI
            "confirmation_policy_logic",     # AlwaysConfirm → ConfirmRisky transition
            "conversation_event_rendering",  # Agent responses rendered
            "agent_execution",               # Mock LLM → agent → action → result
            "scrolling_behavior",            # end key press for consistent snapshot
        },
        "modules_exercised": [
            "textual_app.py",
            "richlog_visualizer.py",
            "conversation_manager.py",   # Runs the agent loop
        ],
    },
    "TestCreatingAgentPlan": {
        "description": "Type 'create a dummy plan' → agent creates task list → render",
        "flow": "Launch → type → submit → agent processes → task list displayed",
        "behaviors": {
            "input_widget_rendering",
            "conversation_event_rendering",
            "agent_execution",
            "task_tracker_rendering",    # Plan/task list display
        },
        "modules_exercised": [
            "textual_app.py",
            "richlog_visualizer.py",
            "conversation_manager.py",
        ],
    },
    "TestEchoHelloWorld": {
        "description": "Full conversation: type → submit → agent echoes → result",
        "flow": "Launch → type 'echo hello world' → submit → wait → snapshot",
        "behaviors": {
            "input_widget_rendering",
            "conversation_event_rendering",
            "agent_execution",
            "terminal_output_rendering", # Terminal command output display
        },
        "modules_exercised": [
            "textual_app.py",
            "richlog_visualizer.py",
            "conversation_manager.py",
        ],
    },
    "TestHistoryPanelFlow": {
        "description": "History panel: open → list conversations → switch → load",
        "flow": "6 phases: open panel → see list → new conv → see 2nd → click prev → load",
        "behaviors": {
            "history_panel_rendering",       # Side panel UI
            "conversation_listing",          # List stored conversations
            "conversation_creation",         # /new command
            "conversation_switching",        # Click to switch
            "conversation_loading",          # Load previous conversation state
            "conversation_persistence",      # Conversations survive switch + reload
            "agent_execution",
        },
        "modules_exercised": [
            "textual_app.py",
            "history_side_panel.py",
            "conversation_manager.py",
            "conversation_switch_controller.py",
            # Implicitly: LocalFileStore (persistence)
        ],
    },
    "TestInitialSetupCancelThenExit": {
        "description": "First-time user → settings modal → cancel → exit modal",
        "flow": "Launch (no config) → settings modal → press Escape → exit modal → quit",
        "behaviors": {
            "first_time_setup_flow",         # No agent_settings.json → show settings
            "settings_modal_rendering",      # Settings screen UI
            "exit_modal_rendering",          # Exit confirmation dialog
            "keyboard_navigation",           # Tab, Escape, Enter key handling
        },
        "modules_exercised": [
            "textual_app.py",
            "settings_screen.py",
        ],
    },
    "TestInitialSetupCancelThenReturn": {
        "description": "First-time user → settings modal → cancel → return to setup",
        "flow": "Launch (no config) → settings modal → Escape → 'Go Back' → settings again",
        "behaviors": {
            "first_time_setup_flow",
            "settings_modal_rendering",
            "exit_modal_rendering",
            "keyboard_navigation",
        },
        "modules_exercised": [
            "textual_app.py",
            "settings_screen.py",
        ],
    },
    "TestMultilineModeSubmit": {
        "description": "Toggle multiline → type → submit with Ctrl+D",
        "flow": "Launch → Ctrl+N (multiline) → type → Ctrl+D (submit) → result",
        "behaviors": {
            "multiline_input_mode",          # Toggle and rendering
            "input_widget_rendering",
            "conversation_event_rendering",
            "agent_execution",
        },
        "modules_exercised": [
            "textual_app.py",
            "richlog_visualizer.py",
            "conversation_manager.py",
        ],
    },
    "TestSelectAll": {
        "description": "Type text → Ctrl+A → verify selection rendering",
        "flow": "Launch → type → Ctrl+A → snapshot shows selected text",
        "behaviors": {
            "input_widget_rendering",
            "text_selection_rendering",
        },
        "modules_exercised": [
            "textual_app.py",
        ],
    },
    "TestSkillsCommand": {
        "description": "Type /skills → see skills list rendered",
        "flow": "Launch → type '/skills' → submit → see skills output",
        "behaviors": {
            "slash_command_handling",         # /skills command processing
            "conversation_event_rendering",
        },
        "modules_exercised": [
            "textual_app.py",
            "richlog_visualizer.py",
        ],
    },
    "TestIterativeRefinementCaseA": {
        "description": "Critic feedback loop: agent → critic scores → refine → complete",
        "flow": "Launch → type → agent runs → critic evaluates → refines → done",
        "behaviors": {
            "conversation_event_rendering",
            "agent_execution",
            "critic_feedback_rendering",     # Critic scores displayed
            "iterative_refinement_flow",     # Refinement loop logic
        },
        "modules_exercised": [
            "textual_app.py",
            "richlog_visualizer.py",
            "conversation_manager.py",
        ],
    },
    "TestIterativeRefinementCaseB": {
        "description": "Critic feedback loop variant B",
        "flow": "Same as case A with different trajectory",
        "behaviors": {
            "conversation_event_rendering",
            "agent_execution",
            "critic_feedback_rendering",
            "iterative_refinement_flow",
        },
        "modules_exercised": [
            "textual_app.py",
            "richlog_visualizer.py",
            "conversation_manager.py",
        ],
    },

    # === Unit-level snapshot tests (isolated widgets, no full app) ===
    "TestExitModalSnapshots": {
        "description": "Exit modal rendered in isolation",
        "flow": "Mount ExitConfirmationScreen → snapshot",
        "behaviors": {
            "exit_modal_rendering",
            "keyboard_navigation",           # Tab navigation hints in footer
        },
        "modules_exercised": [
            "textual_app.py",
        ],
    },
    "TestMCPSidePanelSnapshots": {
        "description": "MCP side panel with various server states",
        "flow": "Mount MCPSidePanel with mock data → snapshot",
        "behaviors": {
            "mcp_panel_rendering",           # MCP server list display
            "mcp_server_status_display",     # Running/failed/mixed states
        },
        "modules_exercised": [
            "mcp_side_panel.py",
        ],
    },
    "TestCriticFeedbackWidgetSnapshots": {
        "description": "Critic feedback widget with various scores",
        "flow": "Mount CriticFeedbackWidget with test data → snapshot",
        "behaviors": {
            "critic_feedback_rendering",
        },
        "modules_exercised": [
            "richlog_visualizer.py",
        ],
    },
    "TestCriticSettingsTabSnapshots": {
        "description": "Critic settings tab with various configurations",
        "flow": "Mount CriticSettingsTab with preset values → snapshot",
        "behaviors": {
            "settings_modal_rendering",
            "critic_settings_display",       # Thresholds, toggles
        },
        "modules_exercised": [
            "settings_screen.py",
        ],
    },
    "TestAutocompleteDropdownSnapshots": {
        "description": "Autocomplete dropdown rendering",
        "flow": "Mount AutocompleteDropdown with suggestions → snapshot",
        "behaviors": {
            "autocomplete_rendering",        # Dropdown suggestions display
        },
        "modules_exercised": [
            "textual_app.py",
        ],
    },
    "TestHistorySearchSnapshots": {
        "description": "History search modal with mock data + filtering",
        "flow": "Mount HistorySearchScreen → snapshot; then with filter → snapshot",
        "behaviors": {
            "history_search_rendering",      # Search modal UI
            "history_search_filtering",      # Filter by keyword
            "prompt_history_loading",        # Load from prompt_history.json
        },
        "modules_exercised": [
            # Implicitly: locations.py (get_prompt_history_path)
        ],
    },
    "TestVisualizerSnapshots": {
        "description": "ConversationVisualizer with finish/think actions",
        "flow": "Mount VisualizerTestApp with mock events → snapshot",
        "behaviors": {
            "conversation_event_rendering",
            "action_padding_alignment",      # Visual alignment of actions
        },
        "modules_exercised": [
            "richlog_visualizer.py",
        ],
    },
}


# =============================================================================
# Bug classification
# =============================================================================

# For each Category B bug (file has coverage but PR didn't touch snapshots),
# map the bug to the behavior it fixes and check if any snapshot test
# exercises that behavior.
#
# This mapping was built by reading each bug-fix PR title + rationale from
# categorized_prs.csv and matching against the COVERAGE_MAP behaviors.

# Classification rules:
#   B1 (indirectly covered) requires ALL of:
#     1. A snapshot test exercises a flow that runs through this code path
#     2. The specific bug would produce VISIBLY DIFFERENT snapshot output
#     3. The test's input data (trajectory/mock data) would trigger the bug
#   If any condition fails, it's B2 (adjacent but not covered).
#
# Common B2 patterns:
#   - Behavior category matches but specific variant/edge case isn't in test data
#   - Same module exercised but a different code path within it
#   - Race conditions / environment-specific bugs that deterministic tests can't hit
#   - Features added after the snapshot tests were written

BUG_BEHAVIOR_MAP = {
    # PR# → (classification, rationale)
    # We classify directly rather than mapping to abstract behaviors,
    # because the question is: would THIS bug change a snapshot?

    # --- B1: snapshot output WOULD change if this bug existed ---
    297: ("B1", "Confirmation panel scrolling broken — TestConfirmationMode phase 6 scrolls to end via pilot.press('end'). If scrolling failed, the final snapshot would show content stuck at top instead of bottom."),
    473: ("B1", "Conversation switch handling broken — TestHistoryPanelFlow exercises 6 phases including switching between conversations. A switch failure would produce a visibly different snapshot (wrong conversation loaded)."),
    335: ("B1", "Long terminal commands not truncated in collapsed view — TestEchoHelloWorld renders terminal command output. The 'echo hello world' trajectory produces collapsed CmdOutputObservation widgets. If truncation broke, long commands would overflow visibly in the snapshot."),
    546: ("B1", "Conditional autoscrolling broken — TestConfirmationMode phase 6 calls pilot.press('end'). TestEchoHelloWorld waits for agent completion. Both produce snapshots where scroll position matters. Broken autoscroll would show content at wrong position."),

    # --- B2: behavior category matches but snapshot wouldn't catch THIS bug ---
    286: ("B2", "Markup parsing in user messages — TestEchoHelloWorld renders agent responses but the test trajectory's user message is plain 'echo hello world'. This bug only triggers with special chars like [bold] in user text. No trajectory contains markup chars in user input, so the snapshot wouldn't change."),
    375: ("B2", "Command palette capitalization — the command palette is opened via Ctrl+P, which no snapshot test exercises. Tests exercise input_widget_rendering but never open the command palette overlay."),
    388: ("B2", "Headless mode env overrides — snapshot tests launch the full TUI app, never headless mode. Different initialization path entirely."),
    400: ("B2", "MessageEvent display when critic is enabled — the test trajectories don't include this specific event type (ConversationErrorEvent). The rendering code IS exercised but this event type never appears in test data, so the snapshot wouldn't differ."),
    448: ("B2", "Missing posthog module — this is an ImportError in a specific environment. Snapshot tests run in a controlled env where posthog IS available. A deterministic test can't catch a missing-dependency bug."),
    695: ("B2", "Event loop race condition — snapshot tests are fully deterministic (mock LLM + trajectory replay + await). A race condition between async tasks can't be triggered in the serial test execution model."),
    732: ("B2", "Markup in conversation notifications — notifications are toast-style overlays that appear and disappear. No snapshot test captures a notification mid-display, and the notification text isn't part of the main rendered content."),

    # --- B2: settings_persistence — no snapshot exercises save→reload flow ---
    280: ("B2", "model_id not preserved on load — TestInitialSetup tests first-time flow (no saved settings). No snapshot test saves settings then reopens to verify persistence."),
    311: ("B2", "Notification after settings change — no snapshot renders notifications."),
    322: ("B2", "Model name formatting for litellm — choices.py formats names for dropdowns. No snapshot test renders the provider/model dropdown populated with real data."),
    372: ("B2", "Memory condensation not editable — settings_screen.py condenser tab. TestCriticSettingsTab covers critic settings tab but NOT the condensation settings tab."),
    510: ("B2", "Memory condensation default value — similar to #372, critic settings tab is tested but condensation tab is not."),
    514: ("B2", "Settings modal .clear() behavior — no snapshot tests clearing settings and verifying the result."),
    645: ("B2", "Typo in notification message — no snapshot renders this notification."),
    697: ("B2", "Stale max tokens on model identity change — no snapshot exercises the model-switch → max-tokens-update flow."),
    714: ("B2", "Provider sort order in dropdown — no snapshot renders the populated provider dropdown."),
}


def is_tui(r):
    return r.get("llm_touches_tui", "").lower() == "true" or r.get("h_touches_tui", "") == "true"


def get_all_covered_behaviors():
    """Return the set of all behaviors covered by any snapshot test."""
    behaviors = set()
    for suite in COVERAGE_MAP.values():
        behaviors |= suite["behaviors"]
    return behaviors


def classify_bug(pr_number: int) -> tuple[str, str]:
    """Classify a Category B bug as B1 (indirectly covered), B2 (adjacent), or B3 (unrelated).

    Returns (classification, explanation).
    """
    if pr_number not in BUG_BEHAVIOR_MAP:
        return "B3", "Bug not mapped — PR not in behavior map"

    classification, rationale = BUG_BEHAVIOR_MAP[pr_number]
    return classification, rationale


def load_data(csv_path, json_path):
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    with open(json_path) as f:
        full = {p["pr_number"]: p for p in json.load(f)["pull_requests"]}
    return rows, full


def get_category_b_bugs(rows, full):
    """Get Category B bugs: TUI bug-fixes post-adoption where file has coverage but PR didn't touch snapshots."""
    rows_by_date = sorted(rows, key=lambda r: r.get("pr_created_at", ""))

    # Build covered file set
    covered_files = set()
    for r in rows_by_date:
        if r["h_has_snapshot_changes"] == "true":
            pr = full.get(int(r["pr_number"]), {})
            covered_files |= set(
                f["filename"] for f in pr.get("pr_files", [])
                if f["filename"].endswith(".py")
                and "snapshot" not in f["filename"].lower())

    cat_b = []
    for r in rows_by_date:
        if r.get("pr_created_at", "") < "2026-01":
            continue
        if r["llm_pr_type"] != "bug-fix" or not is_tui(r):
            continue
        if r["h_has_snapshot_changes"] == "true":
            continue  # Category A

        pr = full.get(int(r["pr_number"]), {})
        code_files = set(
            f["filename"] for f in pr.get("pr_files", [])
            if f["filename"].endswith(".py")
            and "snapshot" not in f["filename"].lower()
            and not f["filename"].endswith(".svg"))

        if code_files & covered_files:
            cat_b.append(r)

    return cat_b


def generate_graph(b1_count, b2_count, b3_count, cat_a_count, cat_c_count, out_dir):
    """Generate the reclassified coverage graph."""
    fig, ax = plt.subplots(figsize=(10, 5))

    labels = [
        "A: Bug path directly\nsnapshot-covered",
        "B1: Indirectly covered\n(snapshot exercises this flow)",
        "B2: Adjacent coverage\n(no test for this behavior)",
        "C: Fully uncovered\narea",
    ]
    values = [cat_a_count, b1_count, b2_count + b3_count, cat_c_count]
    colors = ["#27AE60", "#56CCF2", "#F2994A", "#EB5757"]

    bars = ax.barh(range(len(labels)), values, color=colors, height=0.55, edgecolor="white")
    total = sum(values)
    for bar, v in zip(bars, values):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                f"{v}  ({100 * v / total:.0f}%)", va="center", fontsize=11, fontweight="bold")

    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel("Post-adoption TUI bug-fix PRs")
    ax.set_title(f"Reclassified Path-Level Coverage (n={total})")
    ax.invert_yaxis()
    fig.tight_layout()
    fig.savefig(out_dir / "deep_18_reclassified_coverage.png", dpi=150)
    plt.close(fig)
    print(f"  Graph saved: {out_dir / 'deep_18_reclassified_coverage.png'}")


def main():
    p = argparse.ArgumentParser(description="Analyze indirect snapshot coverage")
    p.add_argument("--input-csv", default="data/categorized_prs.csv")
    p.add_argument("--input-json", default="data/mined_prs.json")
    p.add_argument("--output-dir", default="graphs")
    args = p.parse_args()

    rows, full = load_data(args.input_csv, args.input_json)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Get Category B bugs
    cat_b = get_category_b_bugs(rows, full)
    print(f"Category B bugs (file has coverage, PR didn't touch snapshots): {len(cat_b)}")

    # Classify each
    b1, b2, b3 = [], [], []
    for r in cat_b:
        pr_num = int(r["pr_number"])
        cls, explanation = classify_bug(pr_num)
        entry = {"pr": pr_num, "title": r["pr_title"][:55], "explanation": explanation}
        if cls == "B1":
            b1.append(entry)
        elif cls == "B2":
            b2.append(entry)
        else:
            b3.append(entry)

    print(f"\n  B1 (indirectly covered by snapshot flow): {len(b1)}")
    for e in b1:
        print(f"    #{e['pr']:>4} {e['title']}")
        print(f"          → {e['explanation']}")

    print(f"\n  B2 (adjacent, no test for this behavior): {len(b2)}")
    for e in b2:
        print(f"    #{e['pr']:>4} {e['title']}")
        print(f"          → {e['explanation']}")

    if b3:
        print(f"\n  B3 (unrelated): {len(b3)}")
        for e in b3:
            print(f"    #{e['pr']:>4} {e['title']}")

    # Count Category A and C for the full picture
    rows_by_date = sorted(rows, key=lambda r: r.get("pr_created_at", ""))
    covered_files = set()
    for r in rows_by_date:
        if r["h_has_snapshot_changes"] == "true":
            pr = full.get(int(r["pr_number"]), {})
            covered_files |= set(
                f["filename"] for f in pr.get("pr_files", [])
                if f["filename"].endswith(".py")
                and "snapshot" not in f["filename"].lower())

    cat_a_count = 0
    cat_c_count = 0
    for r in rows_by_date:
        if r.get("pr_created_at", "") < "2026-01":
            continue
        if r["llm_pr_type"] != "bug-fix" or not is_tui(r):
            continue
        if r["h_has_snapshot_changes"] == "true":
            cat_a_count += 1
        else:
            pr = full.get(int(r["pr_number"]), {})
            code_files = set(
                f["filename"] for f in pr.get("pr_files", [])
                if f["filename"].endswith(".py")
                and "snapshot" not in f["filename"].lower()
                and not f["filename"].endswith(".svg"))
            if not (code_files & covered_files):
                cat_c_count += 1

    total = cat_a_count + len(b1) + len(b2) + len(b3) + cat_c_count
    print(f"\n{'='*60}")
    print(f"FULL RECLASSIFIED BREAKDOWN (n={total})")
    print(f"{'='*60}")
    print(f"  A  (directly covered):        {cat_a_count:3d} ({100*cat_a_count/total:.0f}%)")
    print(f"  B1 (indirectly covered):      {len(b1):3d} ({100*len(b1)/total:.0f}%)")
    print(f"  B2 (adjacent, not covered):   {len(b2):3d} ({100*len(b2)/total:.0f}%)")
    if b3:
        print(f"  B3 (unrelated):               {len(b3):3d} ({100*len(b3)/total:.0f}%)")
    print(f"  C  (fully uncovered):         {cat_c_count:3d} ({100*cat_c_count/total:.0f}%)")

    actually_covered = cat_a_count + len(b1)
    not_covered = len(b2) + len(b3) + cat_c_count
    print(f"\n  Actually covered (A + B1):     {actually_covered} ({100*actually_covered/total:.0f}%)")
    print(f"  Not covered (B2 + B3 + C):     {not_covered} ({100*not_covered/total:.0f}%)")
    print(f"\n  Previous estimate:             34% covered, 66% uncovered")
    print(f"  Revised estimate:              {100*actually_covered/total:.0f}% covered, {100*not_covered/total:.0f}% uncovered")

    generate_graph(len(b1), len(b2), len(b3), cat_a_count, cat_c_count, out)


if __name__ == "__main__":
    main()
