#!/usr/bin/env python3
# Copyright (c) 2026 Sagar Patni. Released under the MIT License.
"""
Apply generated code guidelines to CLAUDE.local.md and linked category files.

Claude performs the analysis and produces a guidelines JSON spec.
This script handles all file I/O: writing category .md files, updating
CLAUDE.local.md index, and adding entries to .gitignore.

Guidelines JSON spec format (passed via --guidelines-json):
{
  "categories": {
    "<slug>": {
      "title": "Human-readable category title",
      "description": "One-line summary shown in CLAUDE.local.md index",
      "guidelines": [
        {
          "title": "Short imperative rule title",
          "body":  "Full explanation of the guideline.",
          "tags":  ["backend", "python"],
          "code_example": {
            "language": "python",
            "bad":  "(optional) snippet showing the problematic pattern",
            "good": "(optional) snippet showing the correct pattern"
          }
        }
      ]
    }
  }
}

Usage:
    python3 apply_guidelines.py --guidelines-json '<json>' [--guidelines-dir .claude-guidelines]

Extract comment threads from latest results file for Claude to analyze:
    python3 apply_guidelines.py --extract [--results-dir .]
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone


GITIGNORE_ENTRIES = [".git_learn/", "CLAUDE.local.md"]

CLAUDE_LOCAL_HEADER = """\
# Local Code Guidelines

<!-- Auto-generated from code review history by git_learn skill.
     Each session appends new guidelines — nothing is ever removed. -->

## How to use

Before starting any task, read the category file(s) below that are
relevant to what you are about to work on.

Each guideline carries one or more tags (e.g. `backend`, `frontend`,
`python`, `infra`). Use the tags to filter: load a file only when at
least one of its tags matches your current task context. You do not
need to read all files — only the ones whose tags are relevant.

For example:
- Writing backend Python with async code → load files tagged `backend`, `python`, or `concurrency`
- Reviewing a React component → load files tagged `frontend` or `typescript`
- Reviewing infrastructure config → load files tagged `infra` or `cloud`

Only load files where at least one tag matches — skip the rest.

## Categories

"""


# ---------------------------------------------------------------------------
# Extract mode — output comment threads for Claude to analyse
# ---------------------------------------------------------------------------

def find_latest_results(results_dir: str) -> str | None:
    pattern = os.path.join(results_dir, "results_*.json")
    files = sorted(glob.glob(pattern), reverse=True)
    return files[0] if files else None


def extract_comments(results_dir: str) -> None:
    path = find_latest_results(results_dir)
    if not path:
        print(f"ERROR: No results_*.json found in '{results_dir}'.", file=sys.stderr)
        sys.exit(1)

    with open(path) as f:
        results = json.load(f)

    threads = []
    for sha, commit in results.items():
        for thread in commit.get("comment_threads", []):
            threads.append({
                "commit_sha": sha[:7],
                "commit_message": commit.get("message", ""),
                "thread": thread,
            })

    print(json.dumps({"results_file": path, "comment_threads": threads}, indent=2))


# ---------------------------------------------------------------------------
# Apply mode — write guidelines files
# ---------------------------------------------------------------------------

def existing_guideline_titles(path: str) -> set[str]:
    """Return lowercase titles of guidelines already in a category file."""
    if not os.path.exists(path):
        return set()
    titles: set[str] = set()
    with open(path) as f:
        for line in f:
            m = re.match(r"^##\s+(.+)", line.strip())
            if m:
                titles.add(m.group(1).strip().lower())
    return titles


def collect_tags_from_file(path: str) -> list[str]:
    """Return sorted union of all tags across every guideline in a category file."""
    if not os.path.exists(path):
        return []
    tags: set[str] = set()
    with open(path) as f:
        for line in f:
            m = re.match(r"^\*\*Tags:\*\*\s+(.+)", line.strip())
            if m:
                tags.update(re.findall(r"`([^`]+)`", m.group(1)))
    return sorted(tags)


def write_category_file(path: str, title: str, guidelines: list[dict]) -> tuple[int, int]:
    """Append new guidelines to a category file. Returns (added, skipped)."""
    existing = existing_guideline_titles(path)
    to_write: list[str] = []

    for g in guidelines:
        g_title = g.get("title", "").strip()
        if not g_title:
            continue
        if g_title.lower() in existing:
            pass  # counted as skipped below
        else:
            tags = g.get("tags", [])
            tag_line = ("**Tags:** " + " ".join(f"`{t}`" for t in tags) + "\n\n") if tags else ""
            block = f"\n## {g_title}\n\n{tag_line}{g.get('body', '').strip()}\n"
            ex = g.get("code_example")
            if ex:
                lang = ex.get("language", "")
                if ex.get("bad"):
                    block += f"\n**Avoid:**\n```{lang}\n{ex['bad'].strip()}\n```\n"
                if ex.get("good"):
                    block += f"\n**Prefer:**\n```{lang}\n{ex['good'].strip()}\n```\n"
            to_write.append(block)

    added = len(to_write)
    skipped = len(guidelines) - added

    if to_write:
        mode = "a" if os.path.exists(path) else "w"
        with open(path, mode) as f:
            if mode == "w":
                f.write(f"# {title}\n")
            f.writelines(to_write)

    return added, skipped


def existing_category_slugs(claude_local_path: str) -> set[str]:
    """Parse existing CLAUDE.local.md and return slugs that already have a link."""
    if not os.path.exists(claude_local_path):
        return set()
    slugs: set[str] = set()
    with open(claude_local_path) as f:
        for line in f:
            m = re.match(r"^-\s+\[[^\]]+\]\(([^)]+)\)", line.strip())
            if m:
                slug = os.path.splitext(os.path.basename(m.group(1)))[0]
                slugs.add(slug)
    return slugs


def update_claude_local(
    claude_local_path: str,
    categories: dict,
    guidelines_dir: str,
    session_ts: str,
) -> int:
    """Sync category links in CLAUDE.local.md: add new ones, refresh tags on existing ones.

    Returns count of newly added category links.
    """
    existing = existing_category_slugs(claude_local_path)
    new_slugs = [s for s in categories if s not in existing]
    refresh_slugs = [s for s in categories if s in existing]

    # Rewrite tag portion on lines for categories that already exist
    if refresh_slugs and os.path.exists(claude_local_path):
        with open(claude_local_path) as f:
            lines = f.readlines()
        updated: list[str] = []
        for line in lines:
            matched = next((s for s in refresh_slugs if f"{s}.md)" in line), None)
            if matched:
                tags = collect_tags_from_file(os.path.join(guidelines_dir, f"{matched}.md"))
                # Strip old tag section (everything from ' — `' to end of line)
                base = re.sub(r"\s*—\s*`[^\n]*", "", line.rstrip("\n"))
                tag_str = (" — " + " ".join(f"`{t}`" for t in tags)) if tags else ""
                updated.append(f"{base}{tag_str}\n")
            else:
                updated.append(line)
        with open(claude_local_path, "w") as f:
            f.writelines(updated)

    if not new_slugs:
        return 0

    if not os.path.exists(claude_local_path):
        with open(claude_local_path, "w") as f:
            f.write(CLAUDE_LOCAL_HEADER)

    with open(claude_local_path, "a") as f:
        f.write(f"\n<!-- {session_ts} -->\n")
        for slug in new_slugs:
            cat = categories[slug]
            tags = collect_tags_from_file(os.path.join(guidelines_dir, f"{slug}.md"))
            tag_str = (" — " + " ".join(f"`{t}`" for t in tags)) if tags else ""
            rel = os.path.join(guidelines_dir, f"{slug}.md")
            f.write(f"- [{cat['title']}]({rel}) — {cat['description']}{tag_str}\n")

    return len(new_slugs)


def update_gitignore(extra_entries: list[str]) -> None:
    path = ".gitignore"
    existing: set[str] = set()
    if os.path.exists(path):
        with open(path) as f:
            existing = {line.strip() for line in f}

    to_add = [e for e in extra_entries if e not in existing]
    if not to_add:
        return

    with open(path, "a") as f:
        f.write("\n# git_learn skill — auto-generated\n")
        for entry in to_add:
            f.write(entry + "\n")


def apply_guidelines(guidelines_json: str, guidelines_dir: str, results_file: str | None) -> None:
    try:
        spec = json.loads(guidelines_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid guidelines JSON: {e}", file=sys.stderr)
        sys.exit(1)

    categories: dict = spec.get("categories", {})
    if not categories:
        print("No categories provided — nothing to write.")
        return

    os.makedirs(guidelines_dir, exist_ok=True)
    session_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"\nWriting guidelines to {guidelines_dir}/ …\n")

    total_added = total_skipped = 0
    for slug, cat in categories.items():
        cat_path = os.path.join(guidelines_dir, f"{slug}.md")
        added, skipped = write_category_file(cat_path, cat["title"], cat.get("guidelines", []))
        total_added += added
        total_skipped += skipped
        print(f"  {cat_path:<50}  +{added} new  /  {skipped} already existed")

    new_links = update_claude_local("CLAUDE.local.md", categories, guidelines_dir, session_ts)
    update_gitignore(GITIGNORE_ENTRIES)

    print()
    print(f"  CLAUDE.local.md   — {new_links} new category link(s) added")
    print(f"  .gitignore        — entries verified")
    print(f"  Guidelines total  — {total_added} added, {total_skipped} skipped (already existed)")

    if results_file:
        if os.path.exists(results_file):
            os.remove(results_file)
            print(f"  {results_file:<50}  deleted (guidelines applied)")
        else:
            print(f"  {results_file:<50}  not found — skipping delete")

    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract review comments or apply generated guidelines to CLAUDE.local.md."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--extract",
        action="store_true",
        help="Extract comment threads from the latest results_*.json for Claude to analyse.",
    )
    mode.add_argument(
        "--guidelines-json",
        metavar="JSON",
        help="JSON spec of guidelines to apply (produced by Claude after analysing comments).",
    )
    parser.add_argument(
        "--results-dir",
        default=".git_learn",
        metavar="DIR",
        help="Directory to search for results_*.json files (default: .git_learn/).",
    )
    parser.add_argument(
        "--guidelines-dir",
        default=".git_learn",
        metavar="DIR",
        help="Directory for category .md files (default: .git_learn/).",
    )
    parser.add_argument(
        "--delete-results",
        metavar="PATH",
        default=None,
        help="Path to the results_*.json file to delete after guidelines are applied.",
    )
    args = parser.parse_args()

    if args.extract:
        extract_comments(args.results_dir)
    else:
        apply_guidelines(args.guidelines_json, args.guidelines_dir, args.delete_results)


if __name__ == "__main__":
    main()
