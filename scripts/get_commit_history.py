#!/usr/bin/env python3
# Copyright (c) 2026 Sagar Patni. Released under the MIT License.
"""
Fetch GitHub commit history for an author with PR review thread comments.

Comments are fetched via the GitHub GraphQL API so that the `is_resolved`
flag on each review thread is available. Commits without associated PRs
will have empty comment_threads.

Persistence:
  .git_learn/commit_seen.txt              — permanent; one SHA per line.
  .git_learn/results_<ts>_<pid>.json     — per-session; new commits only.

Usage:
    # Last 100 commits (default), author inferred from git config
    python3 get_commit_history.py --owner <owner> --repo <repo> --token <token>

    # Last 50 commits for a specific author
    python3 get_commit_history.py --owner <owner> --repo <repo> \
        --author <github-username> --token <token> --last 50

    # All commits from all authors (use the sentinel value "all")
    python3 get_commit_history.py --owner <owner> --repo <repo> \
        --author all --token <token>

    # All commits from all authors, no limit
    python3 get_commit_history.py --owner <owner> --repo <repo> \
        --author all --token <token> --last 0
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# GitHub REST helper  (used only for the commit list)
# ---------------------------------------------------------------------------

def github_get(url: str, token: str) -> dict | list:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "claude-git_learn-skill/1.0")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 401:
            print("ERROR: GitHub token invalid or expired (401).", file=sys.stderr)
            print(
                "Regenerate at: GitHub → Settings → Developer settings → Personal access tokens",
                file=sys.stderr,
            )
        elif e.code == 403:
            print("ERROR: Forbidden (403). Rate-limited or insufficient token scopes.", file=sys.stderr)
            print("Required scopes: 'repo' (private) or 'public_repo' (public).", file=sys.stderr)
        elif e.code == 404:
            print(f"ERROR: Not found (404): {url}", file=sys.stderr)
            print("Check owner/repo name and that your token has access.", file=sys.stderr)
        else:
            print(f"ERROR: GitHub API HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# GitHub GraphQL helper  (used for PR review threads + is_resolved)
# ---------------------------------------------------------------------------

_PR_REVIEW_QUERY = """
query($owner: String!, $repo: String!, $sha: GitObjectID!) {
  repository(owner: $owner, name: $repo) {
    object(oid: $sha) {
      ... on Commit {
        associatedPullRequests(first: 10) {
          nodes {
            number
            reviewThreads(first: 100) {
              nodes {
                isResolved
                comments(first: 100) {
                  nodes {
                    author { login }
                    body
                    path
                    line
                    originalLine
                    createdAt
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def graphql_request(query: str, variables: dict, token: str) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=payload, method="POST"
    )
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "claude-git_learn-skill/1.0")
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode())
        if "errors" in result:
            for err in result["errors"]:
                print(f"ERROR: GraphQL: {err.get('message', err)}", file=sys.stderr)
            sys.exit(1)
        return result.get("data", {})
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 401:
            print("ERROR: GitHub token invalid or expired (401).", file=sys.stderr)
        elif e.code == 403:
            print("ERROR: Forbidden (403). Check token scopes.", file=sys.stderr)
        else:
            print(f"ERROR: GraphQL HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


def get_pr_review_threads(owner: str, repo: str, sha: str, token: str) -> list[dict]:
    """Return all PR review threads for a commit, each with isResolved flag."""
    data = graphql_request(_PR_REVIEW_QUERY, {"owner": owner, "repo": repo, "sha": sha}, token)
    obj = (data.get("repository") or {}).get("object") or {}
    prs = (obj.get("associatedPullRequests") or {}).get("nodes", [])
    threads = []
    for pr in prs:
        for thread in (pr.get("reviewThreads") or {}).get("nodes", []):
            threads.append(thread)
    return threads


def serialize_thread(thread: dict) -> dict | None:
    """
    Convert a GraphQL review thread into the stored structure.
    The first comment is the root; subsequent comments are replies.
    is_resolved is inherited from the thread and applied to every node.
    """
    comments = (thread.get("comments") or {}).get("nodes", [])
    if not comments:
        return None

    is_resolved = thread.get("isResolved", False)
    root = comments[0]
    path = root.get("path")
    line = root.get("line") or root.get("originalLine")

    return {
        "is_resolved": is_resolved,
        "author": (root.get("author") or {}).get("login", "unknown"),
        "file": path,
        "line": line,
        "text": (root.get("body") or "").strip(),
        "replies": [
            {
                "is_resolved": is_resolved,
                "author": (r.get("author") or {}).get("login", "unknown"),
                "file": path,
                "line": line,
                "text": (r.get("body") or "").strip(),
                "replies": [],
            }
            for r in comments[1:]
        ],
    }


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def collect_commit_data(entry: dict, owner: str, repo: str, token: str) -> dict:
    sha = entry["sha"]
    message = entry["commit"]["message"].splitlines()[0]
    raw_threads = get_pr_review_threads(owner, repo, sha, token)
    threads = [t for t in (serialize_thread(th) for th in raw_threads) if t]
    return {
        "message": message,
        "comment_threads": threads,
    }


# ---------------------------------------------------------------------------
# Persistence: commit_seen.txt
# ---------------------------------------------------------------------------

def load_seen_shas(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {line.strip() for line in f if line.strip()}


def append_seen_shas(path: str, shas: list[str]) -> None:
    with open(path, "a") as f:
        for sha in shas:
            f.write(sha + "\n")


# ---------------------------------------------------------------------------
# Persistence: results_<timestamp>_<pid>.json
# ---------------------------------------------------------------------------

def make_results_path(results_dir: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pid = os.getpid()
    return os.path.join(results_dir, f"results_{ts}_{pid}.json")


def save_results(path: str, results: dict) -> None:
    with open(path, "w") as f:
        json.dump(results, f, indent=2)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

W = 72


def sep(char: str = "─") -> None:
    print(char * W)


def print_comment(cm: dict, indent: int = 4) -> None:
    pad = " " * indent
    is_reply = indent > 4
    marker = "└─ reply" if is_reply else "┌─"

    print()
    if not is_reply:
        status = "[RESOLVED]" if cm.get("is_resolved") else "[OPEN]"
        print(f"{pad}{marker} @{cm['author']}  {status}")
        if cm.get("file"):
            loc = f"{cm['file']}:{cm['line']}" if cm.get("line") else cm["file"]
            print(f"{pad}│  File: {loc}")
    else:
        print(f"{pad}{marker} @{cm['author']}")

    body_lines = cm["text"].splitlines() or ["(empty)"]
    for i, text in enumerate(body_lines):
        is_last = i == len(body_lines) - 1
        prefix = f"{pad}└─ " if is_last and not cm["replies"] else f"{pad}│  "
        print(f"{prefix}{text}")

    for reply in cm["replies"]:
        print_comment(reply, indent + 4)


def print_commit(sha: str, data: dict) -> None:
    sep()
    print(f"  Commit  : {sha[:7]}  ({sha})")
    print(f"  Message : {data['message']}")
    threads = data["comment_threads"]
    if threads:
        resolved = sum(1 for t in threads if t.get("is_resolved"))
        open_ = len(threads) - resolved
        print(f"\n  Comments ({len(threads)} thread(s)  ·  {resolved} resolved  ·  {open_} open):")
        for root in threads:
            print_comment(root, indent=4)
    else:
        print("\n  Comments: none")


def print_skipped(sha: str, entry: dict, seen_file: str) -> None:
    sep()
    message = entry["commit"]["message"].splitlines()[0]
    print(f"  Commit  : {sha[:7]}  ({sha})  [skipped — already in {seen_file}]")
    print(f"  Message : {message}")


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def resolve_author() -> str:
    for key in ("user.name", "user.email"):
        try:
            result = subprocess.run(
                ["git", "config", key],
                capture_output=True, text=True, check=True,
            )
            value = result.stdout.strip()
            if value:
                return value
        except subprocess.CalledProcessError:
            pass
    return ""


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run(
    owner: str,
    repo: str,
    author: str | None,
    token: str,
    last: int | None,
    seen_file: str,
    results_dir: str,
) -> None:
    scope_label = f"last {last}" if last else "all"
    author_label = author if author else "all authors"
    print(f"\nFetching {scope_label} commit(s) by '{author_label}' in {owner}/{repo} …\n")

    base_url = f"https://api.github.com/repos/{owner}/{repo}/commits?per_page=100"
    if author:
        base_url += f"&author={urllib.parse.quote(author)}"
    commits: list = []
    page = 1
    while True:
        batch = github_get(f"{base_url}&page={page}", token)
        if not batch:
            break
        commits.extend(batch)
        if last and len(commits) >= last:
            commits = commits[:last]
            break
        if len(batch) < 100:
            break
        page += 1

    if not commits:
        if author:
            print(f"No commits found for author '{author}' in {owner}/{repo}.")
            print("\nTips:")
            print("  • Use the exact GitHub username (e.g. octocat), not a display name.")
            print("  • Or use the commit email address (e.g. octocat@github.com).")
        else:
            print(f"No commits found in {owner}/{repo}.")
        return

    os.makedirs(os.path.dirname(seen_file) or ".", exist_ok=True)
    os.makedirs(results_dir, exist_ok=True)

    seen_shas = load_seen_shas(seen_file)

    sep("═")
    print(f"  {len(commits)} commit(s) ({scope_label})  ·  author: {author_label}  ·  {owner}/{repo}")
    print(f"  {len(seen_shas)} SHA(s) already in {seen_file}")
    sep("═")

    results: dict = {}
    new_shas: list[str] = []
    skipped = 0
    with_comments = 0

    for entry in commits:
        sha = entry["sha"]
        print()
        if sha in seen_shas:
            print_skipped(sha, entry, seen_file)
            skipped += 1
            continue

        data = collect_commit_data(entry, owner, repo, token)
        print_commit(sha, data)
        results[sha] = data
        new_shas.append(sha)
        if data["comment_threads"]:
            with_comments += 1

    if new_shas:
        append_seen_shas(seen_file, new_shas)

    results_path = make_results_path(results_dir)
    save_results(results_path, results)

    print()
    sep("═")
    print(f"  Summary  : {len(commits)} commit(s) shown")
    print(f"             {len(new_shas)} new  ·  {skipped} skipped (already seen)  ·  {with_comments} with comments")
    print(f"  Seen     : {seen_file}  ({len(seen_shas) + len(new_shas)} total SHAs)")
    print(f"  Results  : {results_path}  ({len(results)} new commit(s))")
    sep("═")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch GitHub commit history with PR review thread comments (resolved/open). "
            "Skips commits already in commit_seen.txt. "
            "Writes new results to a timestamped results_<ts>_<pid>.json."
        )
    )
    parser.add_argument("--owner", required=True, help="GitHub repository owner (user or org)")
    parser.add_argument("--repo", required=True, help="GitHub repository name")
    parser.add_argument(
        "--author",
        default=None,
        help="GitHub username or commit email. Defaults to git config user.name / user.email.",
    )
    parser.add_argument("--token", required=True, help="GitHub personal access token")
    parser.add_argument(
        "--last",
        type=int,
        default=100,
        metavar="N",
        help="Fetch only the N most recent commits (default: 100). Set to 0 for all commits.",
    )
    parser.add_argument(
        "--seen-file",
        default=".git_learn/commit_seen.txt",
        metavar="PATH",
        help="Path to the persistent seen-SHAs file (default: .git_learn/commit_seen.txt).",
    )
    parser.add_argument(
        "--results-dir",
        default=".git_learn",
        metavar="DIR",
        help="Directory to write per-session results files (default: .git_learn/).",
    )
    args = parser.parse_args()

    if args.author and args.author.lower() == "all":
        author = None  # all-authors mode: no author filter
    else:
        author = args.author or resolve_author()
        if not author:
            print(
                "ERROR: Could not determine author. "
                "Pass --author <github-username-or-email> or --author all for all authors.",
                file=sys.stderr,
            )
            sys.exit(1)

    last = None if args.last == 0 else args.last

    run(
        owner=args.owner,
        repo=args.repo,
        author=author,
        token=args.token,
        last=last,
        seen_file=args.seen_file,
        results_dir=args.results_dir,
    )


if __name__ == "__main__":
    main()
