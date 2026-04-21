```
╭──────────────────────────────────────────────────────────────────────────────────╮
│  ● ● ●                                                                           │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│   ██████╗ ██╗████████╗        ██╗     ███████╗  █████╗ ██████╗  ███╗   ██╗       │
│  ██╔════╝ ██║╚══██╔══╝        ██║     ██╔════╝ ██╔══██╗██╔══██╗ ████╗  ██║       │
│  ██║  ███╗██║   ██║           ██║     █████╗   ███████║██████╔╝  ██╔██╗ ██║      │
│  ██║   ██║██║   ██║           ██║     ██╔══╝   ██╔══██║██╔══██╗  ██║╚██╗██║      │
│  ╚██████╔╝██║   ██║  ▄▄▄▄▄▄  ███████╗███████╗ ██║  ██║██║  ██║  ██║ ╚████║       │
│   ╚═════╝ ╚═╝   ╚═╝           ╚══════╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝  ╚═╝  ╚═══╝       │
│                                                                                  │
│  · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · │
│                                                                                  │
│       ✦  turn your code review history into Claude guidelines  ✦                 │
│                                                                                  │
╰──────────────────────────────────────────────────────────────────────────────────╯
```

Fetches GitHub commit history for an author with PR review thread comments (resolved/open status, file, line, threaded replies). Derives generic code guidelines from review comments and writes them into a lightweight `CLAUDE.local.md` + linked `.md` files under `.git_learn/`.

## How it works

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        /git-learn [author] [n]                      │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                ┌──────────────────▼──────────────────┐
                │         GitHub REST + GraphQL        │
                │  commits · PR threads · isResolved   │
                └──────────────────┬──────────────────┘
                                   │
               ┌───────────────────▼────────────────────┐
               │         commit_seen.txt filter          │
               │   skip already-processed SHAs           │
               └───────────────────┬────────────────────┘
                                   │  new commits only
                ┌──────────────────▼──────────────────┐
                │     results_<ts>_<pid>.json          │
                │  threads · file · line · resolved?   │
                └──────────────────┬──────────────────┘
                                   │
          ┌────────────────────────▼────────────────────────┐
          │               Claude analyses threads            │
          │  resolved → firm rule   |  open → recommendation │
          │  skips nitpicks · duplicates · style-only        │
          │  infers tags: backend · python · concurrency …  │
          └────────────────────────┬────────────────────────┘
                                   │  proposes guidelines
                ┌──────────────────▼──────────────────┐
                │          User confirms               │
                │        yes / no / edit               │
                └──────────────────┬──────────────────┘
                                   │
          ┌────────────────────────▼──────────────────────────┐
          │                   Applied                          │
          │                                                    │
          │  .git_learn/error-handling.md  ← new guidelines   │
          │  .git_learn/concurrency.md     ← new guidelines   │
          │                                                    │
          │  CLAUDE.local.md  ← index with aggregated tags    │
          │  .gitignore       ← entries verified               │
          │  results_*.json   ← deleted                        │
          └────────────────────────────────────────────────────┘
```

## How Claude Code loads skills vs commands

Claude Code has two distinct mechanisms:

| Location | Purpose | Invocation |
| --- | --- | --- |
| `~/.claude/commands/git-learn.md` | Registers `/git-learn` as a slash command the **user** types | `/git-learn [args]` |
| `~/.claude/skills/git_learn/git-learn.md` | Loaded **contextually by Claude** when the task matches the description — never directly invocable | Automatic |

This skill is installed only under `~/.claude/commands/` as a flat `.md` file. The `skills/` directory is intentionally not used to avoid a duplicate entry.

## Install

Run from the directory containing the `git_learn` folder:

```bash
mkdir -p ~/.claude/commands
sed 's|\${CLAUDE_PLUGIN_DIR}|'"$(pwd)/git_learn"'|g' \
  git_learn/git-learn.md > ~/.claude/commands/git-learn.md
```

This generates a flat command file pointing directly at the scripts in this directory — no files are copied. Restart Claude Code after installing.

## Sync after updates

Any time `git-learn.md` is edited, re-run the same command from the same directory:

```bash
sed 's|\${CLAUDE_PLUGIN_DIR}|'"$(pwd)/git_learn"'|g' \
  git_learn/git-learn.md > ~/.claude/commands/git-learn.md
```

## Usage

```
/git-learn [author] [n|all]
```

| Invocation | Author | Commits fetched |
| --- | --- | --- |
| `/git-learn` | git config user | last 100 |
| `/git-learn octocat` | octocat | last 100 |
| `/git-learn octocat 50` | octocat | last 50 |
| `/git-learn octocat all` | octocat | all |
| `/git-learn all` | **all users** | last 100 |
| `/git-learn all 50` | **all users** | last 50 |
| `/git-learn all all` | **all users** | all |

## Requirements

- Python 3.10+
- `git` CLI in PATH
- GitHub personal access token with `repo` scope (private) or `public_repo` scope (public)

```bash
export GITHUB_TOKEN=ghp_yourTokenHere
```


<!-- Copyright (c) 2026 Sagar Patni. Released under the MIT License. -->