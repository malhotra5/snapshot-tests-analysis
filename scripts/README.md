# PR Analysis Scripts

Two scripts for mining and categorizing GitHub PR data. Managed with [uv](https://docs.astral.sh/uv/).

## Setup

```bash
uv sync
```

## 1. `mine_github.py` — Data Mining

Fetches comprehensive metadata for every merged PR: GitHub API fields, file lists, reviews, commits, linked issues, and release tag mapping. Saves checkpoints every 20 PRs and resumes from where it left off if interrupted.

```bash
# Quick test (5 PRs):
uv run scripts/mine_github.py --token $GITHUB_TOKEN --repo OpenHands/OpenHands-CLI --limit 5 --git-dir /path/to/repo

# Full repo (~310 PRs, ~10 min):
uv run scripts/mine_github.py --token $GITHUB_TOKEN --repo OpenHands/OpenHands-CLI --all --git-dir /path/to/repo

# Resume after interruption (just re-run the same command):
uv run scripts/mine_github.py --token $GITHUB_TOKEN --repo OpenHands/OpenHands-CLI --all --git-dir /path/to/repo
```

**Requires:** `gh` CLI authenticated, `git` (for release mapping), `curl`.

**Output:** `mined_prs.json` — one JSON record per PR with 40+ fields.

### Fields mined per PR

| Category | Fields |
|----------|--------|
| **PR metadata** | number, title, body, author, state, created/merged/closed timestamps, additions, deletions, changed files, labels, head/base branch, review decision, is_draft |
| **Files** | List of {filename, status, additions, deletions} for every file in the PR |
| **Reviews** | List of {user, state, submitted_at} for every review |
| **Commits** | List of {sha, message, author} for every commit |
| **Linked issues** | List of {number, title, body, author, labels, created_at} for closing issues (via GraphQL) |
| **Release** | release_tag, release_date (mapped via `git log` between consecutive tags) |
| **Computed** | hours_to_merge, hours_to_first_review, authorship (agent/human/bot), agent marker flags, file-path signals (touches_tui, touches_snapshots, touches_tests), linked issue summary |

## 2. `categorize_prs.py` — Classification + CSV Export

Takes the mined JSON, applies heuristic keyword labels and optionally LLM classification, exports a flat CSV.

```bash
# Heuristic only (no LLM, instant):
uv run scripts/categorize_prs.py --input mined_prs.json --heuristic-only

# With LLM classification:
uv run scripts/categorize_prs.py --input mined_prs.json --api-key $OPENAI_API_KEY --model gpt-4o-mini

# Custom LLM endpoint:
uv run scripts/categorize_prs.py --input mined_prs.json --api-key $KEY --model my-model --base-url https://my-llm/v1
```

**Output:** `categorized_prs.csv` — 45 columns per PR.

### Labels added

| Label | Values | Method |
|-------|--------|--------|
| `h_pr_type` | bug-fix, feature, refactor, test, ci, docs, dependency-bump, version-bump, other | Keyword heuristic |
| `h_bug_severity` | critical, ux-regression, polish, other-fix | Keyword heuristic (bug-fix PRs only) |
| `h_touches_tui` | true/false | File path check |
| `h_authorship` | agent, human, bot | PR body markers + author |
| `h_has_snapshot_changes` | true/false | File path check |
| `h_issue_reporter_type` | human, bot, self-filed, no-issue | Issue author vs PR author |
| `llm_pr_type` | (same as h_pr_type) | LLM classification |
| `llm_bug_severity` | critical, ux-regression, polish, not-applicable | LLM classification |
| `llm_touches_tui` | true/false | LLM classification |
| `llm_rationale` | Free text | LLM explanation |

## End-to-end example

```bash
uv sync

# Mine everything
uv run scripts/mine_github.py \
  --token $GITHUB_TOKEN \
  --repo OpenHands/OpenHands-CLI \
  --all \
  --git-dir /path/to/OpenHands-CLI \
  --output data/mined_prs.json

# Categorize with heuristics (fast, no API key needed)
uv run scripts/categorize_prs.py \
  --input data/mined_prs.json \
  --heuristic-only \
  --output data/categorized_prs.csv

# Or categorize with LLM for better accuracy
uv run scripts/categorize_prs.py \
  --input data/mined_prs.json \
  --api-key $OPENAI_API_KEY \
  --model gpt-4o-mini \
  --output data/categorized_prs.csv
```
