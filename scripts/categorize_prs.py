#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["openhands-sdk>=1.0"]
# ///
"""
Categorize mined PR data using an LLM (via openhands-sdk) and export to CSV.

Uses the OpenHands SDK's LLM object, which wraps litellm and supports 100+
providers with built-in retry logic and telemetry.

Usage (via uv):
    # Categorize with LLM (any litellm-supported model):
    uv run scripts/categorize_prs.py --input mined_prs.json --model gpt-4o-mini

    # Anthropic:
    uv run scripts/categorize_prs.py --input mined_prs.json --model anthropic/claude-sonnet-4-20250514

    # Ollama (local):
    uv run scripts/categorize_prs.py --input mined_prs.json --model ollama/llama3

    # Custom endpoint:
    uv run scripts/categorize_prs.py --input mined_prs.json --model my-model --base-url https://my-llm/v1

    # Explicit API key (otherwise uses env vars like OPENAI_API_KEY, ANTHROPIC_API_KEY):
    uv run scripts/categorize_prs.py --input mined_prs.json --model gpt-4o-mini --api-key $OPENAI_API_KEY

    # Skip LLM, just export mined data + heuristic labels to CSV:
    uv run scripts/categorize_prs.py --input mined_prs.json --heuristic-only

Output: categorized_prs.csv
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Heuristic (keyword-based) classification — always available, no LLM needed
# ---------------------------------------------------------------------------

BUG_KEYWORDS = ["fix", "bug", "crash", "hotfix"]
FEAT_KEYWORDS = ["feat", "add", "implement", "support"]
REFACTOR_KEYWORDS = ["refactor", "cleanup", "reorganize", "simplify", "consolidate"]
TEST_KEYWORDS = ["test", "snapshot", "e2e", "mock"]
CI_KEYWORDS = ["ci", "workflow", "github action", "pre-commit"]
DOCS_KEYWORDS = ["doc", "readme", "changelog", "agents.md"]
DEPS_KEYWORDS = ["bump", "upgrade", "dependency", "dependabot"]

CRITICAL_KEYWORDS = ["crash", "error", "exception", "broken", "fails to",
                      "no running event loop", "no module named", "traceback"]
UX_KEYWORDS = ["display", "hidden", "truncat", "scroll", "layout", "visible",
               "reset", "preserve", "respect", "clear", "capitali", "navigation",
               "paste", "wrapping"]
POLISH_KEYWORDS = ["typo", "type hint", "return type", "composeresult",
                    "deterministic", "brittle", "unnecessary", "unused",
                    "consolidate", "duplicate", "alias", "rename"]


def heuristic_pr_type(title, labels, files):
    """Classify PR type from title/labels/files."""
    tl = title.lower()
    label_names = [l.lower() for l in labels]

    if "bump version" in tl or "bump sdk" in tl:
        return "version-bump"
    if any(k in tl for k in DEPS_KEYWORDS) or "dependencies" in label_names:
        return "dependency-bump"
    if any(k in tl for k in BUG_KEYWORDS) or "bug" in label_names:
        return "bug-fix"
    if any(k in tl for k in FEAT_KEYWORDS):
        return "feature"
    if any(k in tl for k in REFACTOR_KEYWORDS):
        return "refactor"

    # Check files for test-only or CI-only changes
    filenames = [f["filename"] for f in files] if files else []
    if all(f.startswith("tests/") or f.startswith("tui_e2e/") for f in filenames if f):
        return "test"
    if all(f.startswith(".github/") for f in filenames if f):
        return "ci"
    if any(k in tl for k in CI_KEYWORDS):
        return "ci"
    if any(k in tl for k in TEST_KEYWORDS):
        return "test"
    if any(k in tl for k in DOCS_KEYWORDS):
        return "docs"

    return "other"


def heuristic_bug_severity(title):
    """Classify bug severity from title keywords."""
    tl = title.lower()
    if any(k in tl for k in CRITICAL_KEYWORDS):
        return "critical"
    if any(k in tl for k in UX_KEYWORDS):
        return "ux-regression"
    if any(k in tl for k in POLISH_KEYWORDS):
        return "polish"
    return "other-fix"


def heuristic_classify(rec):
    """Apply all heuristic labels to a PR record. Returns a flat dict of labels."""
    title = rec.get("pr_title", "")
    labels = rec.get("pr_labels", [])
    files = rec.get("pr_files", [])

    pr_type = heuristic_pr_type(title, labels, files)
    bug_severity = heuristic_bug_severity(title) if pr_type == "bug-fix" else ""

    # Issue reporter type
    issue_reporter = "no-issue"
    if rec.get("has_linked_issue") and rec.get("linked_issue_authors"):
        issue_authors = rec["linked_issue_authors"].split(",")
        if any(a == rec.get("pr_author", "") for a in issue_authors):
            issue_reporter = "self-filed"
        elif any(a in ("all-hands-bot", "github-actions[bot]", "app/github-actions")
                 for a in issue_authors):
            issue_reporter = "bot"
        else:
            issue_reporter = "human"

    return {
        "h_pr_type": pr_type,
        "h_bug_severity": bug_severity,
        "h_touches_tui": rec.get("touches_tui_files", False),
        "h_authorship": rec.get("authorship", "human"),
        "h_has_snapshot_changes": rec.get("touches_snapshot_files", False),
        "h_issue_reporter_type": issue_reporter,
    }


# ---------------------------------------------------------------------------
# LLM classification
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a software engineering analyst classifying pull requests.
 
 Given a PR's title, description, file list, and linked issues, output a JSON object with:

{
  "pr_type": one of "bug-fix", "feature", "refactor", "test", "ci", "docs", "dependency-bump", "version-bump", "other",
  "bug_severity": (only if pr_type is "bug-fix") one of "critical", "ux-regression", "polish", "not-applicable",
  "touches_tui": true/false — does this PR modify terminal UI code (widgets, modals, screens, CSS, layout, rendering)?
  "rationale": a 1-2 sentence explanation of your classification
}

Definitions:
- critical: the bug causes an app crash, data loss, exception, or makes a feature completely unusable.
- ux-regression: the bug causes wrong visual output, hidden elements, truncation, broken navigation, or layout problems — the app runs but something looks or behaves wrong.
- polish: the bug is about code quality (type hints, typos, test flakiness, dead code removal) — no user-visible behavior change.
"""


def build_llm_prompt(rec):
    """Build the user prompt for LLM classification."""
    files = rec.get("pr_files", [])
    file_list = "\n".join(f"  {f['filename']} (+{f['additions']}/-{f['deletions']})"
                          for f in files[:30])

    issues_text = ""
    for iss in rec.get("linked_issues", []):
        issues_text += f"\n  Issue #{iss['number']}: {iss['title']}\n"
        if iss.get("body"):
            issues_text += f"    {iss['body'][:300]}\n"

    return f"""PR #{rec['pr_number']}: {rec['pr_title']}

Author: {rec['pr_author']} (authorship: {rec.get('authorship', '?')})
Labels: {', '.join(rec.get('pr_labels', [])) or 'none'}
Lines: +{rec.get('pr_additions', 0)} / -{rec.get('pr_deletions', 0)} across {rec.get('pr_changed_files_count', 0)} files

Description (first 800 chars):
{(rec.get('pr_body') or '')[:800]}

Files changed:
{file_list or '  (none available)'}

Linked issues:{issues_text or ' none'}
"""


def make_llm(model, api_key=None, base_url=None):
    """Create an OpenHands SDK LLM instance."""
    from openhands.sdk import LLM

    kwargs = {"model": model}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return LLM(**kwargs)


def call_llm(llm, prompt):
    """Call the LLM for classification and return parsed JSON."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    try:
        resp = llm.completion(messages=messages)
        content = resp.message.content.strip()
        # Parse JSON from response (handle markdown code blocks)
        if content.startswith("```"):
            content = "\n".join(content.split("\n")[1:-1])
        return json.loads(content)
    except Exception as e:
        print(f"  LLM error: {e}", file=sys.stderr)
        return None


def llm_classify(llm, rec):
    """Classify a PR using the LLM. Returns dict of labels."""
    prompt = build_llm_prompt(rec)
    result = call_llm(llm, prompt)
    if not result:
        return {
            "llm_pr_type": "",
            "llm_bug_severity": "",
            "llm_touches_tui": "",
            "llm_rationale": "LLM call failed",
        }
    return {
        "llm_pr_type": result.get("pr_type", ""),
        "llm_bug_severity": result.get("bug_severity", ""),
        "llm_touches_tui": result.get("touches_tui", ""),
        "llm_rationale": result.get("rationale", ""),
    }


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    # PR metadata
    "pr_number", "pr_title", "pr_author", "pr_state",
    "pr_created_at", "pr_merged_at",
    "pr_additions", "pr_deletions", "pr_changed_files_count",
    "pr_labels", "pr_head_branch", "pr_base_branch",
    "pr_review_decision", "pr_is_draft",
    "pr_review_count", "pr_reviewer_logins", "pr_commit_count",
    # Timing
    "hours_to_merge", "hours_to_first_review",
    # Authorship
    "pr_author", "authorship", "is_bot_author",
    "has_any_agent_marker", "agent_marker_conversation_link",
    "agent_marker_coauthored", "agent_marker_try_pr",
    # File signals
    "touches_tui_files", "touches_snapshot_files", "touches_test_files",
    "snapshot_svgs_changed",
    # Issues
    "has_linked_issue", "linked_issue_count",
    "linked_issue_numbers", "linked_issue_authors",
    # Release
    "release_tag", "release_date",
    # Heuristic labels
    "h_pr_type", "h_bug_severity", "h_touches_tui",
    "h_authorship", "h_has_snapshot_changes", "h_issue_reporter_type",
    # LLM labels (if available)
    "llm_pr_type", "llm_bug_severity", "llm_touches_tui", "llm_rationale",
]


def flatten_for_csv(rec):
    """Flatten a PR record for CSV export."""
    row = {}
    for col in CSV_COLUMNS:
        val = rec.get(col, "")
        if isinstance(val, list):
            val = ",".join(str(v) for v in val)
        if isinstance(val, bool):
            val = str(val).lower()
        row[col] = val
    return row


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Categorize mined PR data")
    p.add_argument("--input", required=True, help="Path to mined_prs.json")
    p.add_argument("--output", default="categorized_prs.csv", help="Output CSV path")
    p.add_argument("--heuristic-only", action="store_true",
                   help="Skip LLM, use keyword heuristics only")
    p.add_argument("--api-key", help="LLM API key (overrides env vars)")
    p.add_argument("--model", default="gpt-4o-mini",
                   help="Any litellm model string, e.g. gpt-4o-mini, "
                        "anthropic/claude-sonnet-4-20250514, ollama/llama3")
    p.add_argument("--base-url", default=None,
                   help="Custom LLM API base URL (e.g. Azure, local proxy)")
    args = p.parse_args()

    # Load
    with open(args.input) as f:
        data = json.load(f)
    prs = data.get("pull_requests", [])
    print(f"Loaded {len(prs)} PRs from {args.input}")

    # Create LLM instance once (reused for all PRs)
    llm = None
    if not args.heuristic_only:
        llm = make_llm(args.model, api_key=args.api_key, base_url=args.base_url)
        print(f"Using model: {args.model}" +
              (f" via {args.base_url}" if args.base_url else ""))

    # Classify
    for i, rec in enumerate(prs):
        num = rec["pr_number"]
        title = rec.get("pr_title", "")[:50]

        # Heuristic labels (always)
        h_labels = heuristic_classify(rec)
        rec.update(h_labels)

        # LLM labels (if requested)
        if llm is not None:
            print(f"  [{i+1}/{len(prs)}] LLM classifying #{num}: {title}")
            llm_labels = llm_classify(llm, rec)
            rec.update(llm_labels)
            if (i + 1) % 20 == 0:
                time.sleep(1)  # rate limit
        else:
            rec["llm_pr_type"] = ""
            rec["llm_bug_severity"] = ""
            rec["llm_touches_tui"] = ""
            rec["llm_rationale"] = ""

    # Export CSV
    # Deduplicate CSV_COLUMNS (pr_author appears twice above)
    seen = set()
    unique_cols = []
    for c in CSV_COLUMNS:
        if c not in seen:
            unique_cols.append(c)
            seen.add(c)

    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=unique_cols, extrasaction="ignore")
        writer.writeheader()
        for rec in prs:
            writer.writerow(flatten_for_csv(rec))

    print(f"\nWrote {len(prs)} rows → {args.output}")

    # Summary
    from collections import Counter
    types = Counter(rec.get("h_pr_type") for rec in prs)
    sevs = Counter(rec.get("h_bug_severity") for rec in prs if rec.get("h_bug_severity"))
    print(f"\nHeuristic PR types: {dict(types)}")
    print(f"Bug severities: {dict(sevs)}")

    if not args.heuristic_only:
        llm_types = Counter(rec.get("llm_pr_type") for rec in prs if rec.get("llm_pr_type"))
        print(f"LLM PR types: {dict(llm_types)}")


if __name__ == "__main__":
    main()
