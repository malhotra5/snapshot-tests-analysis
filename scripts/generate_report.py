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
    --bg: #fafafa;
    --card: #fff;
    --text: #1a1a1a;
    --muted: #666;
    --accent: #5B8DEF;
    --green: #27AE60;
    --red: #EB5757;
    --orange: #F2994A;
    --border: #e5e5e5;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text);
    line-height: 1.6; max-width: 960px; margin: 0 auto;
    padding: 2rem 1.5rem;
  }}
  h1 {{ font-size: 2rem; margin-bottom: 0.25rem; }}
  h1 + p {{ color: var(--muted); margin-bottom: 2rem; font-size: 0.95rem; }}
  h2 {{
    font-size: 1.35rem; margin-top: 3rem; margin-bottom: 0.5rem;
    padding-bottom: 0.4rem; border-bottom: 2px solid var(--border);
  }}
  h2 .num {{ color: var(--muted); font-weight: 400; }}
  h3 {{ font-size: 1.05rem; margin-top: 1.5rem; margin-bottom: 0.4rem; color: var(--text); }}
  p, li {{ margin-bottom: 0.6rem; }}
  ul {{ padding-left: 1.5rem; }}
  .card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 8px; padding: 1.5rem; margin: 1.25rem 0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  }}
  .card img {{
    width: 100%; border-radius: 4px; margin-top: 0.5rem;
  }}
  .card .caption {{
    color: var(--muted); font-size: 0.85rem; margin-top: 0.4rem;
    font-style: italic;
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
    background: var(--card); border: 1px solid var(--border);
    border-radius: 6px;
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
    font-size: 0.82rem; line-height: 1.5; overflow-x: auto;
    margin: 0.75rem 0;
  }}
  .commit-example .snap {{ color: #EB5757; font-weight: bold; }}
  .toc {{ margin: 1.5rem 0 2rem; }}
  .toc a {{ color: var(--accent); text-decoration: none; }}
  .toc a:hover {{ text-decoration: underline; }}
  .toc li {{ margin-bottom: 0.3rem; }}
  footer {{
    margin-top: 3rem; padding-top: 1rem;
    border-top: 1px solid var(--border);
    color: var(--muted); font-size: 0.85rem;
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
  <li><a href="#finding1">Finding 1: Snapshots as a Development Forcing Function</a></li>
  <li><a href="#finding2">Finding 2: Snapshot Tests Break During Development</a></li>
  <li><a href="#finding3">Finding 3: Bug Fixes Update Existing Baselines, Not Add New Ones</a></li>
  <li><a href="#finding4">Finding 4: Faster Reviews for Snapshot PRs</a></li>
  <li><a href="#finding5">Finding 5: Compounding Value Through Suite Reuse</a></li>
  <li><a href="#bugrate">The Coverage Gap: 85% of Bug-Fixes Ship Uncovered</a></li>
  <li><a href="#nobug">What Snapshots Don't Do: Reduce the Bug Count</a></li>
  <li><a href="#verdict">Verdict</a></li>
</ol>
</nav>

<!-- ================================================================ -->
<h2 id="context"><span class="num">§1</span> Context &amp; Dataset</h2>

<p>We mined all {total} merged PRs from the openhands-cli repo (Sep 2025 – May 2026),
classified each by type, authorship, and snapshot involvement, then analyzed
commit-level history to understand <em>how</em> snapshot tests are actually used.</p>

<div style="text-align:center; margin: 1rem 0;">
  <span class="stat blue"><span class="num">{total}</span><span class="label">Total PRs</span></span>
  <span class="stat blue"><span class="num">{agent_pct}%</span><span class="label">Agent-authored</span></span>
  <span class="stat green"><span class="num">{snap_prs}</span><span class="label">Touch snapshots</span></span>
  <span class="stat red"><span class="num">{crit_before}→{crit_after}%</span><span class="label">Critical bug rate</span></span>
</div>

<div class="card">
  <img src="data:image/png;base64,{img_0_summary}" alt="Summary dashboard">
</div>

<div class="card">
  <img src="data:image/png;base64,{img_3_timeline}" alt="Snapshot adoption timeline">
  <p class="caption">Snapshot tests were adopted in January 2026. Before that, zero PRs touched snapshot files.
  After adoption, 19–33% of monthly PRs include snapshot changes.</p>
</div>

<!-- ================================================================ -->
<h2 id="finding1"><span class="num">§2</span> Finding 1: Snapshots as a Development Forcing Function</h2>

<p>The strongest signal in the data. After snapshot adoption, <strong>{tui_feat_snap_pct}% of
TUI-changing feature PRs</strong> now include snapshot updates — up from 0%. This means nearly
every visual change must pass through a "did you mean to change how this looks?" gate.</p>

<div class="evidence positive">
  <strong>What this means:</strong> Snapshot tests create a mandatory visual-diff step in the
  development workflow. Developers (both human and agent) cannot change the TUI without
  consciously acknowledging the visual impact and updating baselines.
</div>

<div class="card">
  <img src="data:image/png;base64,{img_deep4_forcing}" alt="Forcing function over time">
  <p class="caption">The green line shows TUI feature PRs including snapshot updates climbing from 0% to 100%.
  Bug-fix PRs (red) follow at ~35–50% — lower because 35% of all bugs are entirely non-visual
  (API, config, CLI core — see §7), and even among visual bugs, 76% are on uncovered code paths.</p>
</div>

<!-- ================================================================ -->
<h2 id="finding2"><span class="num">§3</span> Finding 2: Snapshot Tests Break During Development</h2>

<p>This is the finding that most surprised us, and it required commit-level data to see.
Of {total_multi} multi-commit snapshot PRs (excluding version bumps), <strong>{code_then_snap}
({code_then_snap_pct}%)</strong> show a clear pattern: code is written first, then a later commit
fixes the snapshot tests.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep3_commit}" alt="Commit-level pattern">
  <p class="caption">{code_then_snap} of {total_multi} multi-commit PRs show the "code first, update snapshots later" pattern —
  evidence that snapshot tests broke during development and had to be fixed before merge.</p>
</div>

<p>This is visible in the commit messages themselves:</p>

<div class="commit-example">
<strong>PR #504</strong> — fix: default collapsibles to collapsed state<br>
[1] fix: default collapsibles to collapsed state<br>
[2] <span class="snap">test: update snapshots for collapsed cells default</span><br>
[3] <span class="snap">fix: add retry mechanism for flaky snapshot test</span><br>
[4] <span class="snap">fix: increase retry count for flaky snapshot test</span>
</div>

<div class="commit-example">
<strong>PR #497</strong> — tui: only show agent name prefix for non-default agents<br>
[1] tui: only show agent name prefix for non-default agents<br>
[2] Update richlog_visualizer.py<br>
[3] test: add tests for default agent prefix behavior<br>
[4] <span class="snap">Fix CI: update TUI snapshot tests and pyright typing</span><br>
[5] <span class="snap">Merge main and update snapshots</span><br>
[7] <span class="snap">Update snapshot tests after merge from main</span>
</div>

<div class="commit-example">
<strong>PR #490</strong> — tui: remove grey vertical border from Collapsible widgets (refactor, 732 lines)<br>
[1] tui: remove grey vertical border<br>
[2] tui: add color to collapse/expand symbol<br>
[3] tui: remove indentation from Collapsible widgets<br>
[4] <span class="snap">fix: use tuple instead of Text and update snapshots</span><br>
[5] style: indent collapsible content with left padding<br>
[7] <span class="snap">fix: update test and snapshots for action/obs default symbol color</span><br>
[10] <span class="snap">Update snapshots after merging from main</span>
</div>

<div class="evidence positive">
  <strong>What this means:</strong> Snapshot tests are functioning as a CI safety net.
  Developers write code, push it, snapshot tests fail in CI, and they come back to
  fix the baselines. This is the "regression catch" behavior — it's just happening
  <em>within a PR</em> rather than between PRs, and it's only visible at the commit level.
</div>

<!-- ================================================================ -->
<h2 id="finding3"><span class="num">§4</span> Finding 3: Bug Fixes Update Existing Baselines, Not Add New Ones</h2>

<p>Of the {snap_bugs_total} bug-fix PRs that include snapshot changes, <strong>{snap_bugs_mod_only}
({snap_bugs_mod_pct}%)</strong> only modify existing snapshot files — they don't add new tests.
This tells us existing snapshot coverage already covered the affected UI states.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep1_pattern}" alt="Bug-fix snapshot pattern">
  <p class="caption">9 of 12 bug-fix PRs only modify existing baselines. These aren't adding
  retroactive test coverage — the tests already existed and their output changed with the fix.</p>
</div>

<div class="card">
  <img src="data:image/png;base64,{img_deep2_counts}" alt="Snapshot file counts by type">
  <p class="caption">Across all PR types, modified files vastly outnumber added files. Version bumps
  are entirely modifications (they update the version string visible in every snapshot).</p>
</div>

<div class="evidence neutral">
  <strong>What this means:</strong> Snapshot tests are acting as regression baselines.
  When a bug fix changes the UI, existing snapshots force an intentional visual diff.
  But this is <em>documentation of the fix</em>, not bug discovery — the bugs themselves
  were found through other means (user reports, manual testing).
</div>

<!-- ================================================================ -->
<h2 id="finding4"><span class="num">§5</span> Finding 4: Faster Reviews for Snapshot PRs</h2>

<p>Despite being substantially larger (median 850 vs 100 lines changed), TUI PRs with
snapshot changes get their <strong>first review nearly twice as fast</strong> (0.9h vs 1.7h).</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep6_review}" alt="Review speed comparison">
  <p class="caption">Left: first-review time is ~2× faster for snapshot PRs.
  Right: those same PRs are ~8× larger. SVG diffs lower the barrier to starting a review.</p>
</div>

<div class="evidence positive">
  <strong>What this means:</strong> SVG snapshot diffs give reviewers instant visual understanding
  of what changed, which is especially valuable for agent-authored code where the reviewer
  didn't write the code. The visual diff substitutes for manually running the app to verify
  the UI.
</div>

<!-- ================================================================ -->
<h2 id="finding5"><span class="num">§6</span> Finding 5: Compounding Value Through Suite Reuse</h2>

<p>The {total_suites} snapshot test suites aren't write-once artifacts. The top suites are
exercised by 15–23 different PRs each, meaning their regression baselines are continuously
validated as the codebase evolves.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep5_reuse}" alt="Test suite reuse">
  <p class="caption">HistoryPanelFlow is touched by 23 PRs. Every one of those PRs had to pass
  (or intentionally update) that visual baseline.</p>
</div>

<div class="card">
  <img src="data:image/png;base64,{img_deep8_coverage}" alt="Suite coverage vs bugs">
  <p class="caption">12 of 23 suites have been involved in bug-fix PRs (right bars).
  HistoryPanelFlow and InitialSetup suites appear in the most bug-fixes — these are the
  high-churn areas where snapshot regression coverage pays off most.</p>
</div>

<!-- ================================================================ -->
<h2 id="bugrate"><span class="num">§7</span> The Coverage Gap: 85% of Bug-Fixes Ship Uncovered</h2>

<p>First, where do bugs actually live? Not all bugs are visual — and snapshot tests can only help
with bugs that change rendered output.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep16_domain}" alt="Bug-fix domain breakdown">
  <p class="caption">The largest category is TUI widgets & screens (38), where 12 involved snapshots (green).
  But 27 bugs (35%) are entirely non-visual: CLI core, config/state, API/auth. Snapshot tests
  are structurally irrelevant for these. Even within the visual categories, settings logic (7) and
  other TUI (6) have zero snapshot involvement.</p>
</div>

<div class="card">
  <img src="data:image/png;base64,{img_deep17_severity}" alt="Severity by domain">
  <p class="caption">Critical bugs skew non-visual (44%) — crashes, auth failures, dependency issues.
  UX regressions are predominantly visual (75%), which is where snapshots have the most theoretical
  coverage. But even there, only a fraction actually involves snapshots.</p>
</div>

<h3>The coverage gap within visual bugs</h3>

<p>Focusing on bugs where snapshots <em>could</em> help, we need to frame bug-fixes into three categories:</p>
<ul>
  <li><strong>No snapshots touched</strong> — the bug was in an uncovered area before, and the fix
      didn't add coverage. A blind spot that remains blind.</li>
  <li><strong>Modified existing snapshots</strong> — the fix changed visual behavior in an area that
      already had baseline coverage. The change is acknowledged and the new state is locked in.</li>
  <li><strong>Added new snapshots</strong> — genuinely extending coverage to a previously-uncovered area.
      The correct thing to do.</li>
</ul>

<div class="card">
  <img src="data:image/png;base64,{img_deep13_gap}" alt="Bug-fix coverage gap">
  <p class="caption">85% of bug-fix PRs ship without touching snapshots at all.
  Only 1% added new coverage. The team is overwhelmingly fixing bugs without
  closing the coverage gap.</p>
</div>

<h3>Existing snapshots guard behavior during code churn</h3>

<p>The commit-level data reveals the most concrete evidence of snapshot value.
Across feature, bug-fix, refactor, and test PRs, there were <strong>58 distinct commits</strong>
where a developer had to go back and fix snapshot tests that broke after their initial code change.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep14_guarding}" alt="Snapshot guarding behavior">
  <p class="caption">Feature PRs generated the most snapshot-fix commits (24, across 9 PRs).
  Every one of these is evidence: a feature was being built, it changed something visual
  that existing snapshots covered, and the developer had to consciously address it.
  Refactors generated 11 snapshot-fix commits — code restructuring was caught changing visual output.</p>
</div>

<div class="evidence positive">
  <strong>The key finding:</strong> 9 out of 9 multi-commit feature PRs with snapshots had to fix
  snapshot tests AFTER their initial code. 100%. Every single feature that touched
  snapshot-covered areas had existing behavior guarded by the test suite. The snapshots
  didn't prevent the change — they made it visible and forced a conscious decision.
</div>

<h3>Where bugs actually hit: path-level coverage</h3>

<p>The previous version of this analysis defined "covered" too loosely — a bug in <code>textual_app.py</code>
was counted as "covered" just because that file appeared in some snapshot PR. But if the bug-fix
PR didn't update any snapshots, <strong>the specific bug path wasn't covered</strong>.
The file having coverage elsewhere is irrelevant — it's a false sense of security.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep15_covered}" alt="Path-level coverage breakdown">
  <p class="caption">Only 34% of post-adoption TUI bugs (green) hit paths actually guarded by snapshots.
  57% (orange) are in files that have <em>some</em> snapshot coverage, but the specific bug path was uncovered —
  the fix didn't touch snapshots. 9% (red) are in fully uncovered areas. Combining orange + red:
  <strong>66% of bugs are on uncovered code paths.</strong></p>
</div>

<div class="evidence neutral">
  <strong>The "false sense of security" pattern:</strong> 20 of 35 post-adoption TUI bugs are in
  files like <code>settings_screen.py</code> and <code>richlog_visualizer.py</code> that have snapshot
  coverage for <em>some</em> of their behavior — but the bug was on a different code path that
  no snapshot exercises. The file having coverage makes it look protected, but the specific
  behavior that broke had no baseline.
</div>

<h3>The UX regression rate, honestly normalized</h3>

<p>Measuring "% of agent PRs that are UX bugs" is misleading when monthly sample sizes range from
5 to 63. The right normalization: UX bugs per cumulative TUI feature shipped. This controls
for codebase growth.</p>

<div class="card">
  <img src="data:image/png;base64,{img_deep10_bugrate}" alt="Normalized bug rate">
  <p class="caption">Pre-adoption (grey): the bug-per-feature rate rose from 0 to 0.45 as the codebase grew
  rapidly. Post-adoption (orange): Jan 2026 started at 0.44 (same level — the adoption month
  coincided with a huge feature push). Then it drops to 0.10, 0.02, 0.00, 0.07.
  <strong>The settled post-adoption rate (Feb–Apr) is 4–20× lower than the pre-adoption peak.</strong></p>
</div>

<!-- ================================================================ -->
<h2 id="nobug"><span class="num">§8</span> What Snapshots Don't Do: Reduce the Bug Count</h2>

<p>The data does not support the claim that snapshot tests reduce the overall bug rate.
The TUI bug-to-feature ratio went from <strong>{tui_bug_ratio_before} to {tui_bug_ratio_after}</strong>
after snapshot adoption.</p>

<div class="card">
  <img src="data:image/png;base64,{img_4_severity}" alt="Bug severity before/after">
  <p class="caption">Critical bugs dropped (11.5% → 6.5%) but UX regressions rose (12.6% → 18.5%).
  The severity shift tracks codebase growth, not snapshot adoption.</p>
</div>

<div class="evidence negative">
  <strong>Why not?</strong> Because 85% of bug-fixes don't involve snapshots. You can't prevent
  bugs in areas you don't cover. The problem isn't that snapshot tests don't work —
  it's that only 15% of the codebase's bug surface has them.
</div>

<!-- ================================================================ -->
<h2 id="verdict"><span class="num">§9</span> Verdict</h2>

<div class="verdict">
<h3>Snapshot tests are helpful — but the biggest problem is the coverage gap.</h3>

<p>Where snapshots exist, they work. The evidence is concrete:</p>

<ul>
  <li><strong>100% of feature PRs</strong> in snapshot-covered areas had to fix broken snapshots
      during development (9/9 multi-commit feature PRs). Existing behavior was guarded.</li>
  <li><strong>58 snapshot-fix commits</strong> across 20 PRs show developers being forced to acknowledge
      visual changes — the "code first, fix snapshots later" pattern at the commit level.</li>
  <li><strong>Reviewers respond 2× faster</strong> to snapshot PRs (0.9h vs 1.7h) despite them being 8× larger.
      SVG diffs provide instant visual understanding.</li>
  <li><strong>Top test suites are exercised by 15–23 PRs each</strong>, compounding their regression value
      with every change.</li>
</ul>

<p>But the data is equally clear about the limitation:</p>

<ul>
  <li><strong>85% of bug-fix PRs ship without touching snapshots.</strong> The bug was uncovered before
      the fix, and the fix didn't add coverage. The blind spot remains.</li>
  <li><strong>Only 1 of 78 bug-fix PRs</strong> added new snapshot coverage retroactively.</li>
  <li><strong>The bug rate didn't decrease</strong> — because you can't prevent bugs in areas you don't cover.</li>
</ul>

<p>The value of snapshot tests is in the <em>development process</em> — making visual changes
visible, reviewable, and intentional. This is especially important when an AI agent is
writing the code and can't visually inspect its own output. But the team is underinvesting
in coverage: bugs are being fixed without extending the safety net to prevent recurrence.</p>
</div>

<div class="card">
  <img src="data:image/png;base64,{img_deep9_sdlc}" alt="SDLC impact summary">
  <p class="caption">Evidence strength across SDLC phases. Development forcing function has the
  strongest evidence. Bug prevention has none.</p>
</div>

<footer>
  <p>Analysis of {total} PRs from
  <a href="https://github.com/OpenHands/openhands-cli">openhands-cli</a>.
  Data mined via GitHub API; PR types classified with gpt-4o-mini.
  Graphs generated with matplotlib. Full data and scripts at
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
        crit_before=s["crit_before_pct"],
        crit_after=s["crit_after_pct"],
        tui_feat_snap_pct=s["tui_feat_snap_pct"],
        total_multi=s["total_multi"],
        code_then_snap=s["code_then_snap"],
        code_then_snap_pct=f"{100 * s['code_then_snap'] / max(s['total_multi'], 1):.0f}",
        snap_bugs_total=s["snap_bugs_total"],
        snap_bugs_mod_only=s["snap_bugs_mod_only"],
        snap_bugs_mod_pct=f"{100 * s['snap_bugs_mod_only'] / max(s['snap_bugs_total'], 1):.0f}",
        tui_bug_ratio_before=s["tui_bug_ratio_before"],
        tui_bug_ratio_after=s["tui_bug_ratio_after"],
        total_suites=s["total_suites"],
        # Embedded images
        img_0_summary=embed(g / "0_summary_dashboard.png"),
        img_3_timeline=embed(g / "3_snapshot_adoption_timeline.png"),
        img_4_severity=embed(g / "4_bug_severity_before_after.png"),
        img_6_ratio=embed(g / "6_bug_to_feature_ratio.png"),
        img_deep1_pattern=embed(g / "deep_1_bugfix_snapshot_pattern.png"),
        img_deep2_counts=embed(g / "deep_2_snapshot_file_counts.png"),
        img_deep3_commit=embed(g / "deep_3_commit_level_pattern.png"),
        img_deep4_forcing=embed(g / "deep_4_forcing_function.png"),
        img_deep5_reuse=embed(g / "deep_5_suite_reuse.png"),
        img_deep6_review=embed(g / "deep_6_review_speed.png"),
        img_deep8_coverage=embed(g / "deep_8_suite_coverage_bugs.png"),
        img_deep9_sdlc=embed(g / "deep_9_sdlc_summary.png"),
        img_deep10_bugrate=embed(g / "deep_10_bug_rate_with_context.png"),
        img_deep11_breakdown=embed(g / "deep_11_ux_regression_breakdown.png"),
        img_deep12_involvement=embed(g / "deep_12_ux_snapshot_involvement.png"),
        img_deep13_gap=embed(g / "deep_13_bugfix_coverage_gap.png"),
        img_deep14_guarding=embed(g / "deep_14_snapshot_guarding.png"),
        img_deep15_covered=embed(g / "deep_15_covered_vs_uncovered.png"),
        img_deep16_domain=embed(g / "deep_16_bugfix_domain.png"),
        img_deep17_severity=embed(g / "deep_17_severity_by_domain.png"),
    )

    Path(args.output).write_text(html)
    print(f"Report written to {args.output} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
