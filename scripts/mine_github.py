#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""
Mine GitHub PR, issue, commit, and release data from a repository.

Usage (via uv):
    # Small sample (10 most recent merged PRs):
    uv run scripts/mine_github.py --token $GITHUB_TOKEN --repo OpenHands/OpenHands-CLI --limit 10

    # Full repository:
    uv run scripts/mine_github.py --token $GITHUB_TOKEN --repo OpenHands/OpenHands-CLI --all

    # With local git repo for release tag mapping (recommended):
    uv run scripts/mine_github.py --token $GITHUB_TOKEN --repo OpenHands/OpenHands-CLI --all --git-dir /path/to/repo

Output: mined_prs.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def gh_api(endpoint, token):
    """Call GitHub REST API via curl."""
    cmd = [
        "curl", "-s",
        "-H", f"Authorization: token {token}",
        "-H", "Accept: application/vnd.github+json",
        f"https://api.github.com{endpoint}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def gh_api_paginated(endpoint, token, max_pages=10):
    """Paginate a GitHub list endpoint."""
    items = []
    for page in range(1, max_pages + 1):
        sep = "&" if "?" in endpoint else "?"
        data = gh_api(f"{endpoint}{sep}per_page=100&page={page}", token)
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        items.extend(data)
        if len(data) < 100:
            break
    return items


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def fetch_pr_list(repo, token, limit):
    """Fetch merged PRs using gh CLI."""
    fields = (
        "number,title,body,author,state,createdAt,mergedAt,closedAt,"
        "additions,deletions,changedFiles,labels,headRefName,baseRefName,"
        "reviewDecision,isDraft"
    )
    cmd = [
        "gh", "pr", "list", "--repo", repo, "--state", "merged",
        "--limit", str(limit), "--json", fields,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       env={**os.environ, "GH_TOKEN": token})
    if r.returncode != 0:
        print(f"gh pr list failed: {r.stderr[:300]}", file=sys.stderr)
        sys.exit(1)
    return json.loads(r.stdout)


def fetch_pr_files(repo, pr_number, token):
    files = gh_api_paginated(f"/repos/{repo}/pulls/{pr_number}/files", token, max_pages=5)
    return [
        {
            "filename": f["filename"],
            "status": f.get("status", ""),
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
        }
        for f in (files or [])
        if isinstance(f, dict) and "filename" in f
    ]


def fetch_pr_reviews(repo, pr_number, token):
    reviews = gh_api_paginated(f"/repos/{repo}/pulls/{pr_number}/reviews", token, max_pages=3)
    return [
        {
            "user": r.get("user", {}).get("login", ""),
            "state": r.get("state", ""),
            "submitted_at": r.get("submitted_at", ""),
        }
        for r in (reviews or [])
        if isinstance(r, dict)
    ]


def fetch_pr_commits(repo, pr_number, token):
    commits = gh_api_paginated(f"/repos/{repo}/pulls/{pr_number}/commits", token, max_pages=5)
    return [
        {
            "sha": c["sha"][:12],
            "message": c.get("commit", {}).get("message", "").split("\n")[0],
            "author": c.get("commit", {}).get("author", {}).get("name", ""),
        }
        for c in (commits or [])
        if isinstance(c, dict) and "sha" in c
    ]


def fetch_linked_issues(repo, pr_number, token):
    """Get closing issues via GraphQL."""
    query = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          closingIssuesReferences(first: 10) {
            nodes {
              number title body
              author { login }
              labels(first: 10) { nodes { name } }
              createdAt
            }
          }
        }
      }
    }
    """
    owner, name = repo.split("/")
    cmd = [
        "gh", "api", "graphql",
        "-f", f"query={query}",
        "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={pr_number}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       env={**os.environ, "GH_TOKEN": token})
    if r.returncode != 0:
        return []
    try:
        nodes = json.loads(r.stdout)["data"]["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"]
        return [
            {
                "number": n["number"],
                "title": n["title"],
                "body": (n.get("body") or "")[:2000],
                "author": (n.get("author") or {}).get("login", ""),
                "labels": [l["name"] for l in (n.get("labels") or {}).get("nodes", [])],
                "created_at": n.get("createdAt", ""),
            }
            for n in nodes
        ]
    except (KeyError, TypeError):
        return []


# ---------------------------------------------------------------------------
# Release / tag mapping from local git
# ---------------------------------------------------------------------------

def build_release_map(git_dir):
    """Map PR numbers → first release tag they appeared in."""
    if not git_dir or not Path(git_dir).is_dir():
        return {}, []

    r = subprocess.run(
        ["git", "--no-pager", "tag", "--sort=creatordate",
         "--format=%(creatordate:iso-strict) %(refname:short)"],
        capture_output=True, text=True, cwd=git_dir,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return {}, []

    tags = []
    for line in r.stdout.strip().split("\n"):
        parts = line.split(" ", 1)
        if len(parts) == 2:
            tags.append({"date": parts[0], "tag": parts[1]})

    pr_to_release = {}
    for i, tag_info in enumerate(tags):
        tag = tag_info["tag"]
        rng = tag if i == 0 else f"{tags[i-1]['tag']}..{tag}"
        r = subprocess.run(
            ["git", "--no-pager", "log", "--oneline", rng],
            capture_output=True, text=True, cwd=git_dir,
        )
        for line in (r.stdout.strip().split("\n") if r.stdout.strip() else []):
            match = re.search(r"\(#(\d+)\)", line)
            if match:
                pr_num = int(match.group(1))
                if pr_num not in pr_to_release:
                    pr_to_release[pr_num] = {
                        "release_tag": tag,
                        "release_date": tag_info["date"][:10],
                    }
    return pr_to_release, tags


# ---------------------------------------------------------------------------
# Derived / computed fields
# ---------------------------------------------------------------------------

def detect_agent_markers(body):
    if not body:
        return {}
    return {
        "conversation_link": "app.all-hands.dev/conversations" in body,
        "coauthored_openhands": "co-authored-by: openhands" in body.lower(),
        "try_pr_block": "uvx --python 3.12 git+" in body,
    }


BOT_AUTHORS = frozenset([
    "all-hands-bot", "app/github-actions", "dependabot[bot]",
    "github-actions[bot]", "renovate[bot]",
])


def compute_derived(rec):
    """Add all computed fields to a PR record."""
    # Hours to merge
    rec["hours_to_merge"] = None
    if rec.get("pr_merged_at"):
        try:
            c = datetime.fromisoformat(rec["pr_created_at"].replace("Z", "+00:00"))
            m = datetime.fromisoformat(rec["pr_merged_at"].replace("Z", "+00:00"))
            rec["hours_to_merge"] = round((m - c).total_seconds() / 3600, 2)
        except (ValueError, TypeError):
            pass

    # Agent markers
    markers = detect_agent_markers(rec.get("pr_body", ""))
    rec["agent_marker_conversation_link"] = markers.get("conversation_link", False)
    rec["agent_marker_coauthored"] = markers.get("coauthored_openhands", False)
    rec["agent_marker_try_pr"] = markers.get("try_pr_block", False)
    rec["has_any_agent_marker"] = any(markers.values())

    # Authorship
    author = rec.get("pr_author", "")
    rec["is_bot_author"] = author in BOT_AUTHORS
    if rec["is_bot_author"]:
        rec["authorship"] = "bot"
    elif rec["has_any_agent_marker"]:
        rec["authorship"] = "agent"
    else:
        rec["authorship"] = "human"

    # File-path signals
    filenames = [f["filename"] for f in rec.get("pr_files", [])]
    rec["touches_tui_files"] = any(
        "openhands_cli/tui/" in f or f.endswith(".tcss") or "/widgets/" in f
        for f in filenames
    )
    rec["touches_snapshot_files"] = any(
        "__snapshots__" in f and f.endswith(".svg") for f in filenames
    )
    rec["touches_test_files"] = any(
        f.startswith("tests/") and f.endswith(".py") for f in filenames
    )
    rec["snapshot_svgs_changed"] = sum(
        1 for f in filenames if "__snapshots__" in f and f.endswith(".svg")
    )

    # Linked issues
    issues = rec.get("linked_issues", [])
    rec["has_linked_issue"] = len(issues) > 0
    rec["linked_issue_count"] = len(issues)
    rec["linked_issue_numbers"] = ",".join(str(i["number"]) for i in issues)
    rec["linked_issue_authors"] = ",".join(i.get("author", "") for i in issues)

    # Time to first review
    rec["hours_to_first_review"] = None
    reviews = rec.get("pr_reviews", [])
    if reviews and rec.get("pr_created_at"):
        try:
            c = datetime.fromisoformat(rec["pr_created_at"].replace("Z", "+00:00"))
            times = []
            for rv in reviews:
                if rv.get("submitted_at"):
                    t = datetime.fromisoformat(rv["submitted_at"].replace("Z", "+00:00"))
                    times.append((t - c).total_seconds() / 3600)
            if times:
                rec["hours_to_first_review"] = round(min(times), 2)
        except (ValueError, TypeError):
            pass

    # Review count / reviewers
    rec["pr_review_count"] = len(reviews)
    rec["pr_reviewer_logins"] = list({r["user"] for r in reviews if r.get("user")})
    rec["pr_commit_count"] = len(rec.get("pr_commits", []))

    return rec


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def mine_one_pr(pr_raw, repo, token, release_map):
    """Mine all data for a single PR."""
    num = pr_raw["number"]
    author = (pr_raw.get("author") or {}).get("login", "")

    rec = {
        "pr_number": num,
        "pr_title": pr_raw.get("title", ""),
        "pr_body": (pr_raw.get("body") or "")[:5000],
        "pr_author": author,
        "pr_state": "merged" if pr_raw.get("mergedAt") else pr_raw.get("state", ""),
        "pr_created_at": pr_raw.get("createdAt", ""),
        "pr_merged_at": pr_raw.get("mergedAt", ""),
        "pr_closed_at": pr_raw.get("closedAt", ""),
        "pr_additions": pr_raw.get("additions", 0),
        "pr_deletions": pr_raw.get("deletions", 0),
        "pr_changed_files_count": pr_raw.get("changedFiles", 0),
        "pr_labels": [l["name"] for l in (pr_raw.get("labels") or [])],
        "pr_head_branch": pr_raw.get("headRefName", ""),
        "pr_base_branch": pr_raw.get("baseRefName", ""),
        "pr_review_decision": pr_raw.get("reviewDecision") or "",
        "pr_is_draft": pr_raw.get("isDraft", False),
    }

    # Supplementary fetches
    rec["pr_files"] = fetch_pr_files(repo, num, token)
    rec["pr_reviews"] = fetch_pr_reviews(repo, num, token)
    rec["pr_commits"] = fetch_pr_commits(repo, num, token)
    rec["linked_issues"] = fetch_linked_issues(repo, num, token)

    # Release mapping
    pr_map = release_map[0] if isinstance(release_map, tuple) else release_map
    rel = pr_map.get(num, {})
    rec["release_tag"] = rel.get("release_tag", "")
    rec["release_date"] = rel.get("release_date", "")

    return compute_derived(rec)


def load_checkpoint(output_path):
    """Load previously mined PRs from output file for resume support."""
    if not Path(output_path).exists():
        return {}
    try:
        with open(output_path) as f:
            data = json.load(f)
        already = {r["pr_number"]: r for r in data.get("pull_requests", [])}
        print(f"  Loaded checkpoint: {len(already)} PRs already mined")
        return already
    except (json.JSONDecodeError, KeyError):
        return {}


def save_progress(results, tags, repo, output_path):
    """Write current results to disk (called periodically for crash safety)."""
    output = {
        "metadata": {
            "repository": repo,
            "mined_at": datetime.now().astimezone().isoformat(),
            "pr_count": len(results),
            "releases": [{"tag": t["tag"], "date": t["date"][:10]} for t in tags],
        },
        "pull_requests": results,
    }
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)


def main():
    p = argparse.ArgumentParser(description="Mine GitHub PR data")
    p.add_argument("--token", required=True, help="GitHub token")
    p.add_argument("--repo", required=True, help="owner/repo")
    p.add_argument("--limit", type=int, default=10, help="PR count (default 10)")
    p.add_argument("--all", action="store_true", help="Fetch all merged PRs")
    p.add_argument("--git-dir", default=".", help="Local clone for release mapping")
    p.add_argument("--output", default="mined_prs.json", help="Output path")
    args = p.parse_args()

    limit = 500 if args.all else args.limit

    # Release map
    print(f"Building release map from {args.git_dir} ...")
    release_map = build_release_map(args.git_dir)
    if isinstance(release_map, tuple):
        print(f"  {len(release_map[1])} tags, {len(release_map[0])} PRs mapped")
    else:
        print("  (no tags found)")
    tags = release_map[1] if isinstance(release_map, tuple) else []

    # Resume: load already-mined PRs
    already_mined = load_checkpoint(args.output)

    # PR list
    print(f"Fetching {'all' if args.all else args.limit} merged PRs ...")
    prs_raw = fetch_pr_list(args.repo, args.token, limit=limit)
    print(f"  {len(prs_raw)} PRs returned from GitHub")

    # Separate already-done from to-do
    to_mine = [pr for pr in prs_raw if pr["number"] not in already_mined]
    results = list(already_mined.values())
    print(f"  {len(results)} already mined (checkpoint), {len(to_mine)} remaining\n")

    # Mine remaining PRs
    for i, pr in enumerate(to_mine):
        num = pr["number"]
        title = pr.get("title", "")[:50]
        print(f"  [{i+1}/{len(to_mine)}] #{num}: {title}")
        try:
            rec = mine_one_pr(pr, args.repo, args.token, release_map)
            results.append(rec)
        except Exception as e:
            print(f"    ERROR mining #{num}: {e}", file=sys.stderr)
            # Save what we have and continue
            save_progress(results, tags, args.repo, args.output)
            print(f"    (saved checkpoint with {len(results)} PRs)")
            continue

        # Periodic checkpoint + rate-limit pause
        if (i + 1) % 20 == 0:
            save_progress(results, tags, args.repo, args.output)
            print(f"    (checkpoint saved: {len(results)} PRs, pausing 3s)")
            time.sleep(3)

    # Final save
    save_progress(results, tags, args.repo, args.output)

    # Summary
    auth = {"agent": 0, "human": 0, "bot": 0}
    for r in results:
        auth[r.get("authorship", "human")] += 1
    linked = sum(1 for r in results if r["has_linked_issue"])
    released = sum(1 for r in results if r["release_tag"])
    print(f"\nDone! Wrote {len(results)} PRs → {args.output}")
    print(f"  Authorship: {auth['agent']} agent, {auth['human']} human, {auth['bot']} bot")
    print(f"  With linked issue: {linked}")
    print(f"  Mapped to release: {released}")


if __name__ == "__main__":
    main()
