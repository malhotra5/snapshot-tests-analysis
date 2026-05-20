#!/usr/bin/env python3
"""Trace bug-fix PRs back to the commit (and PR) that introduced the buggy code.

For each bug-fix PR, runs `git blame` on the parent commit (just before the fix
merged) for every line the fix *deleted or modified*. The blamed commit is then
mapped to its originating PR via the `(#NNN)` pattern in squash-merge messages.

Requires a local clone of the target repo.

Usage:
    python scripts/blame_bug_origins.py --repo /path/to/OpenHands-CLI

Output:
    data/bug_origin_blame.csv — one row per bug-fix PR with:
        - pr_number, authorship of the fix
        - origin_pr, origin_author (who introduced the buggy code)
        - blame_confidence: high/medium/low based on coverage
        - blamed_commits: raw commit list for audit
"""

import argparse
import csv
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

DATA_DIR = Path("data")
OUTPUT = DATA_DIR / "bug_origin_blame.csv"

# Match GitHub squash-merge pattern: "some title (#123)"
PR_NUM_RE = re.compile(r"\(#(\d+)\)\s*$")


def build_commit_to_pr_map(repo_dir):
    """Map every commit SHA to its PR number using commit messages."""
    result = subprocess.run(
        ["git", "log", "--format=%H %s", "--all"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    sha_to_pr = {}
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        sha, *msg_parts = line.split(" ", 1)
        msg = msg_parts[0] if msg_parts else ""
        m = PR_NUM_RE.search(msg)
        if m:
            sha_to_pr[sha] = int(m.group(1))
    return sha_to_pr


def build_pr_to_author_map(rows):
    """Map PR number to authorship from categorized_prs.csv."""
    return {r["pr_number"]: r["authorship"] for r in rows}


def find_merge_commit(repo_dir, pr_number, pr_commits):
    """Find the merge commit SHA for a PR on main."""
    # Try finding by commit message pattern (#NNN)
    result = subprocess.run(
        ["git", "log", "--format=%H %s", "--all", "--grep", f"(#{pr_number})"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        sha = line.split(" ", 1)[0]
        return sha

    # Fallback: use the last commit SHA from PR data (may be a branch commit)
    if pr_commits:
        short = pr_commits[-1].get("sha", "")
        if short:
            result = subprocess.run(
                ["git", "rev-parse", short],
                cwd=repo_dir, capture_output=True, text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
    return None


def get_deleted_lines_blame(repo_dir, merge_sha, pr_files):
    """Run git blame on the parent of merge_sha for files the bug-fix modified.

    Returns a list of blamed commit SHAs — one per deleted/modified line.
    We blame the parent commit (merge_sha~1) to see who last touched the
    lines that the bug-fix changed.
    """
    parent = f"{merge_sha}~1"

    # Verify parent exists
    result = subprocess.run(
        ["git", "rev-parse", parent],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []

    # Get the diff of the bug-fix commit to find which lines were changed
    result = subprocess.run(
        ["git", "diff", parent, merge_sha, "--unified=0", "--no-color"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []

    blamed_shas = []
    current_file = None

    for line in result.stdout.split("\n"):
        # Track which file we're in
        if line.startswith("--- a/"):
            current_file = line[6:]
        elif line.startswith("+++ b/"):
            continue
        elif line.startswith("@@"):
            # Parse the old-file line range: @@ -start,count +new_start,count @@
            m = re.match(r"@@ -(\d+)(?:,(\d+))? ", line)
            if not m or not current_file:
                continue
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) else 1
            if count == 0:
                # Pure addition, no old lines to blame
                continue

            # Skip non-code files
            if current_file.endswith((".svg", ".png", ".lock", ".json", ".toml")):
                continue
            if "__snapshots__" in current_file:
                continue

            # Blame these lines at the parent commit
            blame_result = subprocess.run(
                ["git", "blame", "--porcelain",
                 f"-L{start},{start + count - 1}",
                 parent, "--", current_file],
                cwd=repo_dir, capture_output=True, text=True,
            )
            if blame_result.returncode != 0:
                continue

            for bline in blame_result.stdout.split("\n"):
                # Porcelain format: first line of each block is
                # "<sha> <orig_line> <final_line> <num_lines>"
                if bline and len(bline) >= 40 and bline[0] != "\t":
                    parts = bline.split()
                    if len(parts) >= 3 and len(parts[0]) == 40:
                        sha = parts[0]
                        # Skip the "not committed yet" placeholder
                        if not sha.startswith("0000000"):
                            blamed_shas.append(sha)

    return blamed_shas


def classify_origin(blamed_shas, sha_to_pr, pr_to_author, fix_pr_number):
    """Given blamed SHAs, find the most likely originating PR and author."""
    if not blamed_shas:
        return None, None, "no_blame_data", ""

    # Map each blamed SHA to its PR
    pr_counts = Counter()
    unmapped = 0
    for sha in blamed_shas:
        pr_num = sha_to_pr.get(sha)
        if pr_num:
            pr_counts[pr_num] += 1
        else:
            unmapped += 1

    if not pr_counts:
        return None, None, "no_pr_mapped", ",".join(set(blamed_shas))

    # The most-blamed PR is the likely origin
    origin_pr, origin_count = pr_counts.most_common(1)[0]
    total = sum(pr_counts.values()) + unmapped
    coverage = origin_count / total

    if coverage >= 0.5:
        confidence = "high"
    elif coverage >= 0.25:
        confidence = "medium"
    else:
        confidence = "low"

    origin_author = pr_to_author.get(str(origin_pr), "unknown")

    # Build audit string: top 3 blamed PRs
    audit = "; ".join(
        f"#{pr}({ct}/{total})"
        for pr, ct in pr_counts.most_common(3)
    )

    return origin_pr, origin_author, confidence, audit


def main():
    parser = argparse.ArgumentParser(description="Trace bug origins via git blame")
    parser.add_argument("--repo", required=True, help="Path to OpenHands-CLI clone")
    args = parser.parse_args()

    repo_dir = Path(args.repo).resolve()
    if not (repo_dir / ".git").exists():
        print(f"Error: {repo_dir} is not a git repository")
        return

    print("Building commit → PR map...")
    sha_to_pr = build_commit_to_pr_map(repo_dir)
    print(f"  Mapped {len(sha_to_pr)} commits to PRs")

    print("Loading PR data...")
    with open(DATA_DIR / "categorized_prs.csv") as f:
        cat_rows = list(csv.DictReader(f))
    with open(DATA_DIR / "mined_prs.json") as f:
        mined = json.load(f)
    pr_map = {str(p["pr_number"]): p for p in mined["pull_requests"]}
    pr_to_author = build_pr_to_author_map(cat_rows)

    bug_fixes = [r for r in cat_rows
                 if r["llm_pr_type"] == "bug-fix"
                 and r["llm_bug_severity"] in ("critical", "ux-regression")]
    print(f"  {len(bug_fixes)} bug-fix PRs to analyze")

    results = []
    for i, r in enumerate(bug_fixes):
        pr_num = r["pr_number"]
        pr_data = pr_map.get(pr_num, {})
        pr_commits = pr_data.get("pr_commits", [])

        merge_sha = find_merge_commit(repo_dir, pr_num, pr_commits)
        if not merge_sha:
            results.append({
                "pr_number": pr_num,
                "fix_author": r["authorship"],
                "bug_severity": r["llm_bug_severity"],
                "origin_pr": "",
                "origin_author": "",
                "blame_confidence": "no_merge_commit",
                "blamed_prs_audit": "",
                "blamed_lines": 0,
                "pr_title": r["pr_title"],
            })
            print(f"  [{i+1}/{len(bug_fixes)}] #{pr_num}: no merge commit found")
            continue

        blamed_shas = get_deleted_lines_blame(repo_dir, merge_sha, pr_data.get("pr_files", []))
        origin_pr, origin_author, confidence, audit = classify_origin(
            blamed_shas, sha_to_pr, pr_to_author, pr_num
        )

        results.append({
            "pr_number": pr_num,
            "fix_author": r["authorship"],
            "bug_severity": r["llm_bug_severity"],
            "origin_pr": origin_pr or "",
            "origin_author": origin_author or "",
            "blame_confidence": confidence,
            "blamed_prs_audit": audit,
            "blamed_lines": len(blamed_shas),
            "pr_title": r["pr_title"],
        })
        origin_str = f"#{origin_pr} [{origin_author}]" if origin_pr else "unknown"
        print(f"  [{i+1}/{len(bug_fixes)}] #{pr_num} [{r['authorship']}] → {origin_str} ({confidence}, {len(blamed_shas)} lines)")

    # Write output
    OUTPUT.parent.mkdir(exist_ok=True)
    fieldnames = [
        "pr_number", "fix_author", "bug_severity", "origin_pr",
        "origin_author", "blame_confidence", "blamed_prs_audit",
        "blamed_lines", "pr_title",
    ]
    with open(OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\nSaved: {OUTPUT}")

    # Summary
    total = len(results)
    with_origin = [r for r in results if r["origin_pr"]]
    high_conf = [r for r in with_origin if r["blame_confidence"] == "high"]
    print(f"\n{total} bug-fix PRs analyzed:")
    print(f"  {len(with_origin)} traced to an originating PR")
    print(f"  {len(high_conf)} with high confidence")

    from collections import Counter
    origin_authors = Counter(r["origin_author"] for r in with_origin)
    print(f"\nBug origin by author type:")
    for k, v in origin_authors.most_common():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
