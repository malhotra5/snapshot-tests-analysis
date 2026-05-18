#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
Generate a self-contained HTML report from graph PNGs and analysis data.

Usage:
    uv run scripts/generate_report.py
"""

import argparse
import base64
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

SNAP_KEYWORDS = ["snapshot", "snap ", "update snap", "regenerate", "update svg",
                 "update test", "baseline"]


def embed(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def is_tui(r):
    return (r.get("llm_touches_tui", "").lower() == "true"
            or r.get("h_touches_tui", "") == "true")


def compute_stats(rows, full):
    """Compute all stats referenced in the report."""
    s = {}
    s["total"] = len(rows)
    s["agent"] = len([r for r in rows if r["authorship"] == "agent"])
    s["human"] = len([r for r in rows if r["authorship"] == "human"])
    s["snap_prs"] = len([r for r in rows if r["h_has_snapshot_changes"] == "true"])

    before_agent = [r for r in rows if r.get("pr_created_at", "") < "2026-01" and r["authorship"] == "agent"]
    after_agent = [r for r in rows if r.get("pr_created_at", "") >= "2026-01" and r["authorship"] == "agent"]

    before_crit = len([r for r in before_agent if r["llm_pr_type"] == "bug-fix" and r["llm_bug_severity"] == "critical"])
    after_crit = len([r for r in after_agent if r["llm_pr_type"] == "bug-fix" and r["llm_bug_severity"] == "critical"])
    s["crit_before_pct"] = f"{100 * before_crit / max(len(before_agent), 1):.1f}"
    s["crit_after_pct"] = f"{100 * after_crit / max(len(after_agent), 1):.1f}"

    # Bug-fix snapshot pattern
    snap_bugs = [r for r in rows if r["llm_pr_type"] == "bug-fix" and r["h_has_snapshot_changes"] == "true"]
    mod_only = 0
    for r in snap_bugs:
        pr = full.get(int(r["pr_number"]), {})
        files = [f for f in pr.get("pr_files", []) if "snapshot" in f["filename"].lower() or f["filename"].endswith(".svg")]
        added = [f for f in files if f.get("status") == "added"]
        modified = [f for f in files if f.get("status") == "modified"]
        if modified and not added:
            mod_only += 1
    s["snap_bugs_total"] = len(snap_bugs)
    s["snap_bugs_mod_only"] = mod_only

    # Commit-level pattern
    snap_feature_bug = [r for r in rows
                        if r["h_has_snapshot_changes"] == "true"
                        and r["llm_pr_type"] not in ("version-bump", "dependency-bump")
                        and int(r.get("pr_commit_count", 0)) >= 2]
    code_then_snap = 0
    total_multi = 0
    for r in snap_feature_bug:
        pr = full.get(int(r["pr_number"]), {})
        commits = pr.get("pr_commits", [])
        if len(commits) < 2:
            continue
        total_multi += 1
        indices = [i for i, c in enumerate(commits)
                   if any(kw in (c.get("message") or "").lower() for kw in SNAP_KEYWORDS)]
        if indices and all(i > 0 for i in indices):
            code_then_snap += 1
    s["code_then_snap"] = code_then_snap
    s["total_multi"] = total_multi

    # Forcing function
    post_tui_feats = [r for r in rows if r.get("pr_created_at", "") >= "2026-01" and is_tui(r) and r["llm_pr_type"] == "feature"]
    post_tui_feat_snap = [r for r in post_tui_feats if r["h_has_snapshot_changes"] == "true"]
    s["tui_feat_snap_pct"] = f"{100 * len(post_tui_feat_snap) / max(len(post_tui_feats), 1):.0f}"

    # TUI bug ratio
    before_tui = [r for r in before_agent if is_tui(r)]
    after_tui = [r for r in after_agent if is_tui(r)]
    before_tui_bugs = len([r for r in before_tui if r["llm_pr_type"] == "bug-fix"])
    before_tui_feats = len([r for r in before_tui if r["llm_pr_type"] == "feature"])
    after_tui_bugs = len([r for r in after_tui if r["llm_pr_type"] == "bug-fix"])
    after_tui_feats = len([r for r in after_tui if r["llm_pr_type"] == "feature"])
    s["tui_bug_ratio_before"] = f"{before_tui_bugs / max(before_tui_feats, 1):.2f}"
    s["tui_bug_ratio_after"] = f"{after_tui_bugs / max(after_tui_feats, 1):.2f}"

    # Suite reuse
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
    s["total_suites"] = len(suite_counts)
    s["top_suite_reuse"] = max(len(v) for v in suite_counts.values()) if suite_counts else 0

    return s


REPORT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Snapshot Tests in Agent-Driven Development — Analysis Report</title>
<style>
  :root {{
    --bg: #fafafa; --card: #fff; --text: #1a1a1a; --muted: #666;
    --accent: #5B8DEF; --green: #27AE60; --red: #EB5757;
    --orange: #F2994A; --border: #e5e5e5;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text);
    line-height: 1.6; max-width: 960px; margin: 0 auto; padding: 2rem 1.5rem;
  }}
  h1 {{ font-size: 2rem; margin-bottom: 0.25rem; }}
  h1 + p {{ color: var(--muted); margin-bottom: 2rem; font-size: 0.95rem; }}
  h2 {{
    font-size: 1.35rem; margin-top: 3rem; margin-bottom: 0.5rem;
    padding-bottom: 0.4rem; border-bottom: 2px solid var(--border);
  }}
  h2 .num {{ color: var(--muted); font-weight: 400; }}
  h3 {{ font-size: 1.05rem; margin-top: 1.5rem; margin-bottom: 0.4rem; }}
  p, li {{ margin-bottom: 0.6rem; }}
  ul, ol {{ padding-left: 1.5rem; }}
  code {{ background: #f0f0f0; padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.9em; }}
  .card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 8px; padding: 1.5rem; margin: 1.25rem 0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  }}
  .card img {{ width: 100%; border-radius: 4px; margin-top: 0.5rem; }}
  .card .caption {{
    color: var(--muted); font-size: 0.85rem; margin-top: 0.4rem; font-style: italic;
  }}
  .evidence {{
    background: #f0f7ff; border-left: 4px solid var(--accent);
    padding: 0.8rem 1rem; border-radius: 0 6px 6px 0;
    margin: 0.75rem 0; font-size: 0.95rem;
  }}
  .evidence.positive {{ background: #f0faf4; border-color: var(--green); }}
  .evidence.negative {{ background: #fef5f5; border-color: var(--red); }}
  .evidence.neutral {{ background: #fff8f0; border-color: var(--orange); }}
  .stat {{
    display: inline-block; text-align: center; padding: 0.5rem 1.5rem;
    margin: 0.25rem 0.5rem 0.25rem 0;
    background: var(--card); border: 1px solid var(--border); border-radius: 6px;
  }}
  .stat .num {{ font-size: 1.8rem; font-weight: 700; display: block; }}
  .stat .label {{ font-size: 0.8rem; color: var(--muted); }}
  .stat.blue .num {{ color: var(--accent); }}
  .stat.green .num {{ color: var(--green); }}
  .stat.red .num {{ color: var(--red); }}
  .stat.orange .num {{ color: var(--orange); }}
  .verdict {{
    background: var(--card); border: 2px solid var(--accent);
    border-radius: 10px; padding: 1.5rem; margin: 2rem 0;
  }}
  .verdict h3 {{ color: var(--accent); margin-top: 0; }}
  .commit-example {{
    background: #1a1a1a; color: #e0e0e0; padding: 1rem;
    border-radius: 6px; font-family: "SF Mono", Monaco, monospace;
    font-size: 0.82rem; line-height: 1.5; overflow-x: auto; margin: 0.75rem 0;
  }}
  .commit-example .snap {{ color: #EB5757; font-weight: bold; }}
  .toc {{ margin: 1.5rem 0 2rem; }}
  .toc a {{ color: var(--accent); text-decoration: none; }}
  .toc a:hover {{ text-decoration: underline; }}
  .toc li {{ margin-bottom: 0.3rem; }}
  footer {{
    margin-top: 3rem; padding-top: 1rem;
    border-top: 1px solid var(--border); color: var(--muted); font-size: 0.85rem;
  }}
</style>
</head>
<body>

<h1>Do Snapshot Tests Help in Agent-Driven Development?</h1>
<p>An empirical analysis of {total} pull requests in the
<a href="https://github.com/OpenHands/openhands-cli">openhands-cli</a> repository,
where {agent_pct}% of PRs are authored by AI agents.</p>

<nav class="toc">
<strong>Contents</strong>
<ol>
  <li><a href="#context">Context &amp; Dataset</a></li>
  <li><a href="#bugs">Where Do Bugs Live?</a></li>
  <li><a href="#coverage">What's Actually Covered?</a></li>
  <li><a href="#works">Where Coverage Exists, It Works</a></li>
  <li><a href="#limits">The Bug Rate and Its Limits</a></li>
  <li><a href="#gaps">Actionable Gaps</a></li>
  <li><a href="#verdict">Verdict</a></li>
</ol>
</nav>

<!-- ================================================================ -->
<h2 id="context"><span class="num">§1</span> Context &amp; Dataset</h2>

<p>We mined all {total} merged PRs from the openhands-cli repo (Sep 2025 – May 2026),
classified each by type, authorship, and snapshot involvement, then analyzed
commit-level history to understand <em>how</em> snapshot tests are actually used.
Snapshot tests were adopted in January 2026.</p>

<div style="text-align:center; margin: 1rem 0;">
  <span class="stat blue"><span class="num">{total}</span><span class="label">Total PRs</span></span>
  <span class="stat blue"><span class="num">{agent_pct}%</span><span class="label">Agent-authored</span></span>
  <span class="stat green"><span class="num">{snap_prs}</span><span class="label">Touch snapshots</span></span>
</div>

<div class="card">
  <img src="data:image/png;base64,{img_0_summary}" alt="Summary dashboard">
</div>

<div class="card">
  <img src="data:image/png;base64,{img_3_timeline}" alt="Snapshot adoption timeline">
  <p class="caption">Before January 2026, zero PRs touched snapshot files.
  After adoption, 19–33% of monthly PRs include snapshot changes.</p>
</div>

<!-- ================================================================ -->
<h2 id="bugs"><span class="num">§2</span> Where Do Bugs Live?</h2>

<p>Not all bugs are visual — and snapshot tests can only help with bugs that change rendered
output. This distinction is critical: counting all bug-fixes against snapshot coverage
inflates the apparent gap.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep16_domain}" alt="Bug-fix domain breakdown">
  <p class="caption">The largest category is TUI widgets &amp; screens (38), where 12 involved snapshots
  (green). But 27 bugs (35%) are entirely non-visual: CLI core, config/state, API/auth.
  Snapshot tests are structurally irrelevant for these.</p>
</div>

<div class="card">
  <img src="data:image/png;base64,{img_deep17_severity}" alt="Severity by domain">
  <p class="caption">Critical bugs skew non-visual (44%) — crashes, auth failures, dependency
  issues. UX regressions are predominantly visual (75%), the natural domain for snapshot tests.</p>
</div>

<div class="evidence neutral">
  <strong>Framing matters.</strong> "85% of bug-fix PRs ship without touching snapshots" sounds
  damning, but 35% of those bugs are in code that snapshots <em>can't</em> cover — API auth,
  CLI argument parsing, dependency pinning. The honest denominator is the 51 visual bug-fixes,
  where 12 (24%) have snapshot involvement. That's still a coverage gap — but it's a different
  conversation than "85%."
</div>

<!-- ================================================================ -->
<h2 id="coverage"><span class="num">§3</span> What's Actually Covered?</h2>

<p>Among the 51 visual (TUI) bug-fixes, what fraction hit paths that snapshot tests guard?
We analyzed this at three levels of precision.</p>

<h3>Level 1: Did the bug-fix PR touch snapshots?</h3>

<div class="card">
  <img src="data:image/png;base64,{img_deep13_gap}" alt="Bug-fix coverage gap">
  <p class="caption">Of all 78 bug-fixes, 66 (85%) ship without touching snapshots.
  11 (14%) modified existing baselines. Only 1 (1%) added new coverage.</p>
</div>

<h3>Level 2: Path-level analysis</h3>

<p>Zooming into the 35 post-adoption TUI bug-fixes, we checked whether the bug-fix PR touched
the same code <em>paths</em> covered by snapshots — not just the same files.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep15_covered}" alt="Path-level coverage">
  <p class="caption">34% hit directly covered paths (fix PR updates snapshots). 57% are in files
  with <em>some</em> snapshot coverage but the specific bug path was uncovered. 9% are fully uncovered.</p>
</div>

<h3>Level 3: Accounting for indirect coverage</h3>

<p>Snapshot tests don't just test rendering — they implicitly test the full stack behind what's
rendered. A test that renders a conversation after switching implicitly exercises persistence.
We read every snapshot test file (20 test classes + conftest) and asked:
<em>would the snapshot output actually change if this bug existed?</em></p>

<p>Strict criteria for "indirectly covered":</p>
<ol>
  <li>A snapshot test exercises a flow through this code path</li>
  <li>The bug would produce <strong>visibly different</strong> output</li>
  <li>The test's input data would trigger the bug</li>
</ol>

<div class="card">
  <img src="data:image/png;base64,{img_deep18_reclassified}" alt="Reclassified coverage">
  <p class="caption">After accounting for indirect coverage: 46% of TUI bugs hit covered paths
  (up from 34%). 4 bugs are indirectly covered (confirmation scrolling, conversation switching,
  command truncation, autoscrolling). 16 (46%) remain in adjacent-but-uncovered paths.
  <strong>Revised: 46% covered, 54% uncovered.</strong></p>
</div>

<!-- ================================================================ -->
<h2 id="works"><span class="num">§4</span> Where Coverage Exists, It Works</h2>

<p>The evidence for snapshot value is strongest in areas that have coverage.
Three independent signals confirm this.</p>

<h3>Forcing function: every feature must acknowledge visual impact</h3>

<p>After adoption, <strong>{tui_feat_snap_pct}% of TUI-changing feature PRs</strong> include
snapshot updates — up from 0%. Nearly every visual change passes through a
"did you mean to change how this looks?" gate.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep4_forcing}" alt="Forcing function over time">
  <p class="caption">Feature PRs (green) climb to 100% snapshot involvement. Bug-fix PRs (red)
  follow at ~35–50% — lower because many bugs are non-visual or on uncovered paths (see §2–§3).</p>
</div>

<h3>Development guard: 58 "code first, fix snapshots later" commits</h3>

<p>Of {total_multi} multi-commit snapshot PRs, <strong>{code_then_snap} ({code_then_snap_pct}%)</strong>
show code written first, then a later commit fixing broken snapshots — evidence that existing
tests caught unintended visual changes during development.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep14_guarding}" alt="Snapshot guarding behavior">
  <p class="caption">Feature PRs generated the most snapshot-fix commits (24, across 9 PRs).
  Refactors generated 11 — code restructuring was caught changing visual output.</p>
</div>

<div class="commit-example">
<strong>PR #504</strong> — fix: default collapsibles to collapsed state<br>
[1] fix: default collapsibles to collapsed state<br>
[2] <span class="snap">test: update snapshots for collapsed cells default</span><br>
[3] <span class="snap">fix: add retry mechanism for flaky snapshot test</span><br>
[4] <span class="snap">fix: increase retry count for flaky snapshot test</span>
</div>

<div class="evidence positive">
  <strong>100% hit rate:</strong> 9 out of 9 multi-commit feature PRs touching snapshot-covered
  areas had to fix snapshot tests AFTER their initial code. Every single feature that touched
  covered areas had existing behavior guarded.
</div>

<h3>Review speed: 2× faster first review</h3>

<p>PRs with snapshot changes get first review in <strong>0.9 hours</strong> vs <strong>1.7 hours</strong>
for non-snapshot PRs, despite being 8× larger by file count.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep6_review}" alt="Review speed comparison">
  <p class="caption">Snapshot PRs get first review ~2× faster.</p>
</div>

<div class="evidence neutral">
  <strong>Caveat:</strong> This is correlational, not causal. Snapshot PRs may be faster to review
  because SVG diffs provide instant visual understanding — or because they're more routine,
  involve the same reviewers, or have different complexity profiles. The data doesn't distinguish
  these explanations.
</div>

<!-- ================================================================ -->
<h2 id="limits"><span class="num">§5</span> The Bug Rate and Its Limits</h2>

<p>Did snapshot tests reduce the UX regression rate? The answer depends on how you normalize.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep10_bugrate}" alt="Normalized bug rate">
  <p class="caption">UX bugs per cumulative TUI feature shipped. Pre-adoption peaked at 0.45. The
  adoption month (Jan 2026) started at 0.44 — the same level, coinciding with a huge feature push.
  The settled post-adoption rate (Feb–Apr) drops to 0.02–0.10, which is 4–20× lower.</p>
</div>

<p>The raw per-month rate (% of agent PRs that are UX bugs) is misleading because monthly
sample sizes range from 5 to 63. The bug-per-feature normalization controls for codebase growth
and shows a clear drop once the adoption surge settles.</p>

<p>But the overall TUI bug-to-feature ratio went from <strong>{tui_bug_ratio_before} to
{tui_bug_ratio_after}</strong> — essentially flat. Snapshot tests didn't reduce the total bug
count because 54% of visual bugs are on uncovered paths (§3). You can't prevent bugs in
areas you don't cover.</p>

<!-- ================================================================ -->
<h2 id="gaps"><span class="num">§6</span> Actionable Gaps</h2>

<p>The data points to specific, fixable coverage gaps — not a blanket "write more tests" recommendation.</p>

<h3>1. Settings persistence (7 bugs, 1 test would cover)</h3>

<p>7 of 16 adjacent-but-uncovered bugs are in <code>settings_screen.py</code> and related modules.
The repo tests the <em>first-time setup flow</em> (no saved config) and the <em>critic settings tab</em>,
but no test saves settings, closes the modal, and reopens to verify persistence. A single
"save → reload → snapshot" test would indirectly cover model name persistence, condensation
defaults, dropdown ordering, and clear behavior.</p>

<h3>2. Missing event types in test trajectories (4 bugs)</h3>

<p>The e2e snapshot tests use recorded LLM trajectories for deterministic replay. But the current
trajectories don't include <code>ConversationErrorEvent</code>, messages with markup characters,
or critic-enabled <code>MessageEvent</code>. Adding 2–3 trajectories with these event types
would extend coverage to code paths that are already exercised by the test framework but
never triggered by the test data.</p>

<h3>3. Notification rendering (2 bugs)</h3>

<p>No snapshot test captures notification display (toast-style overlays). Settings change
notifications and conversation notifications both had bugs that went uncaught. A snapshot test
that triggers a notification and captures it mid-display would cover this path.</p>

<h3>4. Bug-fix coverage discipline</h3>

<p>Only 1 of 78 bug-fix PRs added new snapshot coverage retroactively.
When a visual bug is fixed, adding a snapshot that captures the corrected rendering
would prevent the exact recurrence — the highest-value coverage extension possible.</p>

<!-- ================================================================ -->
<h2 id="verdict"><span class="num">§7</span> Verdict</h2>

<div class="verdict">
<h3>Snapshot tests work where they exist — but coverage is shallow.</h3>

<p>The evidence for snapshot value is concrete and multi-signal:</p>
<ul>
  <li><strong>100% of feature PRs</strong> in covered areas had to fix broken snapshots during
      development (9/9 multi-commit feature PRs). Existing behavior was guarded.</li>
  <li><strong>58 snapshot-fix commits</strong> across 20 PRs show developers being forced to
      acknowledge visual changes at the commit level.</li>
  <li>The <strong>normalized UX regression rate dropped 4–20×</strong> in the settled post-adoption
      period (Feb–Apr 2026) compared to the pre-adoption peak.</li>
</ul>

<p>But coverage is shallow:</p>
<ul>
  <li><strong>54% of TUI bugs</strong> (after accounting for indirect coverage) are on uncovered
      code paths — mostly settings persistence and untested event types.</li>
  <li><strong>Only 1 of 78 bug-fix PRs</strong> added new snapshot coverage retroactively.</li>
  <li><strong>35% of all bugs are non-visual</strong> — snapshots are structurally irrelevant for
      API crashes, auth failures, and dependency issues.</li>
</ul>

<p>The biggest ROI: a handful of targeted tests (settings save→reload, new event type
trajectories, notification capture) would close the majority of the coverage gap with
minimal effort.</p>
</div>

<div class="card">
  <img src="data:image/png;base64,{img_deep9_sdlc}" alt="SDLC impact summary">
  <p class="caption">Evidence strength across SDLC phases. Development forcing function has the
  strongest evidence. Bug prevention remains weak due to the coverage gap.</p>
</div>

<footer>
  <p>Analysis of {total} PRs from
  <a href="https://github.com/OpenHands/openhands-cli">openhands-cli</a>.
  Data mined via GitHub API; PR types classified with gpt-4o-mini.
  Indirect coverage analysis based on reading all 20 snapshot test source files.
  Full data and scripts at
  <a href="https://github.com/malhotra5/snapshot-tests-analysis">snapshot-tests-analysis</a>.</p>
</footer>

</body>
</html>
"""

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input-csv", default="data/categorized_prs.csv")
    p.add_argument("--input-json", default="data/mined_prs.json")
    p.add_argument("--graphs-dir", default="graphs")
    p.add_argument("--output", default="report.html")
    args = p.parse_args()

    with open(args.input_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    with open(args.input_json) as f:
        full = {p["pr_number"]: p for p in json.load(f)["pull_requests"]}

    s = compute_stats(rows, full)
    g = Path(args.graphs_dir)

    html = REPORT_HTML.format(
        total=s["total"],
        agent_pct=f"{100 * s['agent'] / s['total']:.0f}",
        snap_prs=s["snap_prs"],
        tui_feat_snap_pct=s["tui_feat_snap_pct"],
        total_multi=s["total_multi"],
        code_then_snap=s["code_then_snap"],
        code_then_snap_pct=f"{100 * s['code_then_snap'] / max(s['total_multi'], 1):.0f}",
        tui_bug_ratio_before=s["tui_bug_ratio_before"],
        tui_bug_ratio_after=s["tui_bug_ratio_after"],
        # Embedded images
        img_0_summary=embed(g / "0_summary_dashboard.png"),
        img_3_timeline=embed(g / "3_snapshot_adoption_timeline.png"),
        img_deep4_forcing=embed(g / "deep_4_forcing_function.png"),
        img_deep6_review=embed(g / "deep_6_review_speed.png"),
        img_deep9_sdlc=embed(g / "deep_9_sdlc_summary.png"),
        img_deep10_bugrate=embed(g / "deep_10_bug_rate_with_context.png"),
        img_deep13_gap=embed(g / "deep_13_bugfix_coverage_gap.png"),
        img_deep14_guarding=embed(g / "deep_14_snapshot_guarding.png"),
        img_deep15_covered=embed(g / "deep_15_covered_vs_uncovered.png"),
        img_deep16_domain=embed(g / "deep_16_bugfix_domain.png"),
        img_deep17_severity=embed(g / "deep_17_severity_by_domain.png"),
        img_deep18_reclassified=embed(g / "deep_18_reclassified_coverage.png"),
    )

    Path(args.output).write_text(html)
    print(f"Report written to {args.output} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
