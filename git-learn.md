---
name: git-learn
description: Fetches GitHub commit history for an author with PR review thread comments (resolved/open). Derives reusable coding guidelines from review feedback and writes them to CLAUDE.local.md. Do not use for general git questions unrelated to GitHub commit history or code review analysis.
when_to_use: "Trigger on: 'what did reviewers say about my code', 'show me my recent commits', 'learn from my pull request feedback', 'generate coding guidelines from my review history', 'what code review comments did I get', 'extract learnings from PRs'"
argument-hint: [author] [n|all]
allowed-tools: Bash(git *) Bash(python3 *) Bash(echo *) Bash(cat *) Bash(cd *)
---

<!-- Copyright (c) 2026 Sagar Patni. Released under the MIT License. -->

# Git Commit History with Comments

Retrieve detailed GitHub commit history for an author, including all commit-level comments and the specific files they reference.

## Step 1 — Verify GitHub Token

Check if `GITHUB_TOKEN` is available by running:

```bash
echo "GITHUB_TOKEN_SET=${GITHUB_TOKEN:+yes}"
```

If the output is empty or blank (not `yes`), stop immediately and tell the user:

> **A GitHub personal access token is required.**
>
> Set it before continuing:
> ```bash
> export GITHUB_TOKEN=ghp_yourTokenHere
> ```
>
> Create a token at: **GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)**
> Required scopes: `repo` (private repos) or `public_repo` (public repos only).
>
> Then re-run this skill.

## Step 2 — Detect Repository Details

Identify the current repository by running:

```bash
git remote get-url origin 2>/dev/null || echo "NO_REMOTE"
```

Parse the URL to extract `{owner}` and `{repo}`. Handle both formats:
- SSH: `git@github.com:owner/repo.git`
- HTTPS: `https://github.com/owner/repo.git`

If the output is `NO_REMOTE`, tell the user:
> "This directory is not a GitHub repository with a remote. Run this skill from inside a cloned GitHub repository."

Also detect the local git identity for use as a default author:

```bash
git config user.name 2>/dev/null; git config user.email 2>/dev/null
```

## Step 3 — Parse Arguments

`$ARGUMENTS` may contain up to two positional values: `[author] [n|all]`.

Parse them with this logic:

| Invocation | Author | Limit |
|---|---|---|
| `/git_learn` | git config user.name/email | 100 |
| `/git_learn octocat` | octocat | 100 |
| `/git_learn octocat 50` | octocat | 50 |
| `/git_learn octocat all` | octocat | no limit (fetch all) |
| `/git_learn 50` | git config user.name/email | 50 |
| `/git_learn all` | **all users** | 100 |
| `/git_learn all 50` | **all users** | 50 |
| `/git_learn all all` | **all users** | no limit (fetch all) |

**Disambiguation rules:**
- If the first argument is exactly the word `all`, it means **all users** (no author filter) — not the current user. Set `RESOLVED_AUTHOR` to the sentinel `__ALL__`.
- If the second argument is a number or the word `all`, it is the commit limit.
- Otherwise both tokens are treated as part of an author name string.
- If `RESOLVED_AUTHOR` is still unresolved (no arguments, or only a numeric limit given), fall back to `git config user.name` then `user.email`.

## Step 4 — Run the Python Script

Execute the bundled script. All output lands in `.git_learn/` automatically:

```bash
# Specific author, with a limit (default 100 or user-specified number)
python3 ${CLAUDE_PLUGIN_DIR}/scripts/get_commit_history.py \
  --owner <OWNER> \
  --repo <REPO> \
  --author "<RESOLVED_AUTHOR>" \
  --token "$GITHUB_TOKEN" \
  --last <N>

# Specific author, fetch all commits (user passed "all" as limit)
python3 ${CLAUDE_PLUGIN_DIR}/scripts/get_commit_history.py \
  --owner <OWNER> \
  --repo <REPO> \
  --author "<RESOLVED_AUTHOR>" \
  --token "$GITHUB_TOKEN"

# All users, with a limit — pass --author all
python3 ${CLAUDE_PLUGIN_DIR}/scripts/get_commit_history.py \
  --owner <OWNER> \
  --repo <REPO> \
  --author all \
  --token "$GITHUB_TOKEN" \
  --last <N>

# All users, fetch all commits — pass --author all, omit --last
python3 ${CLAUDE_PLUGIN_DIR}/scripts/get_commit_history.py \
  --owner <OWNER> \
  --repo <REPO> \
  --author all \
  --token "$GITHUB_TOKEN"
```

Omit `--last` entirely when the user requests all commits. Pass `--author all` when `RESOLVED_AUTHOR` is `__ALL__` — the script will then return commits from all contributors. The script creates `.git_learn/` if absent, then writes:
- `.git_learn/commit_seen.txt` — appends new SHAs; persists across sessions.
- `.git_learn/results_<YYYYMMDD_HHMMSS>_<pid>.json` — new commits only; unique per session so parallel runs never collide.

## Step 5 — Present Results

Display the script output. Then summarize:

- Total commits found for the author
- How many had comments
- Any errors or warnings from the script

If the script exits non-zero:
- **401 Unauthorized** → token invalid/expired; ask user to regenerate
- **403 Forbidden** → insufficient token scopes or rate-limited
- **404 Not Found** → wrong owner/repo, or token has no access to this repo

---

## Step 6 — Extract Review Comments for Guideline Analysis

Run the extract mode of the helper script to get all comment threads from the session's results file:

```bash
python3 ${CLAUDE_PLUGIN_DIR}/scripts/apply_guidelines.py --extract
```

This outputs JSON with a `results_file` field (the path of the results file used) and a `comment_threads` array. **Record the `results_file` path — it is needed in Step 9 to delete the file.**

If `comment_threads` is empty, skip to the end — there is nothing to derive guidelines from.

## Step 7 — Analyse Comments and Propose Guidelines

**Handling large results files**

Before reading the results JSON, check its size. If the file is large enough that reading it in one pass would risk truncation or context overload, read it in chunks — but preserve structural integrity: never split inside a JSON object or array. Process each chunk's `comment_threads` entries, derive guidelines incrementally, and merge them into a single spec before moving to Step 8. Update the `.git_learn/` markdown files iteratively as each chunk is processed rather than holding all output in memory until the end.

**Weighting resolved vs open comments**

Each comment thread carries an `is_resolved` flag. Apply these weights when deciding whether to include a thread as a guideline and how strongly to phrase it:

| `is_resolved` | Meaning | Weight |
|---|---|---|
| `true` | Reviewer raised the issue, author fixed it, reviewer confirmed — the mistake was real and corrected | **High** — always derive a guideline; phrase it as a firm rule |
| `false` | Thread still open — may be a genuine issue, a debate, or a nitpick | **Lower** — include only if the concern is clearly non-trivial; phrase it as a recommendation |

Prioritise resolved threads first. If an open thread and a resolved thread point to the same pattern, the resolved thread's phrasing takes precedence.

For each comment thread in the extracted output, identify the **underlying general pattern** — not the specific instance, but the reusable rule. Then group rules into categories.

Use these category slugs (create new ones only if none fit):

| Slug | When to use |
|---|---|
| `error-handling` | exceptions, resource cleanup, null checks, lock management |
| `naming` | variable, function, class, file naming conventions |
| `concurrency` | thread safety, race conditions, async patterns |
| `api-design` | endpoint structure, pagination, response shapes |
| `security` | input validation, auth, secrets, injection |
| `testing` | test coverage, mocking, assertions |
| `code-style` | readability, structure, formatting, comments |
| `performance` | complexity, caching, query optimisation |

**Tagging guidelines**

For each guideline, infer one or more tags from the list below. Tags help future Claude sessions load only the guidelines relevant to the current task context — they are a filter hint, not a strict rule.

Predefined tags (use these when they fit; create new ones when none of the predefined tags describe the guideline well enough):

`frontend` `backend` `database` `api` `mobile` `infra` `cloud` `ci-cd` `data-pipeline` `architecture` `security` `observability` `concurrency` `performance` `python` `typescript` `go` `rust` `java`

Guidance for the LLM: look at the tags to better filter which guidelines are relevant. You are not required to pick from the list — add a new tag if it more precisely describes the guideline. Multiple tags per guideline are encouraged when the rule genuinely spans more than one domain.

**Using file and line number to generate examples**

Each comment thread in the results file includes a `file` path and `line` number pointing to the exact location the reviewer flagged. **Do not perform a code lookup by default.** Only read the file at the flagged line when a concrete code snippet is genuinely necessary to make the guideline actionable — for example, when the rule involves a non-obvious code pattern that cannot be clearly expressed in prose alone. If the rule is clearly understandable without code (naming conventions, design principles, workflow rules), skip the lookup entirely. When a lookup is warranted, use the real code as the `bad` snippet and the corrected version as the `good` snippet.

**Selective extraction — do not create an entry for every comment thread**

Most comment threads do not warrant a guideline. The default should be to **skip** a thread unless it clears the quality bar below. A lean set of high-signal guidelines is far more useful than an exhaustive list — future Claude sessions will read these files and act on them, so noise degrades quality over time.

**Guideline quality bar**

Only include guidelines that are **non-obvious from reading the code alone** — rules that Claude would likely miss without the reviewer's feedback. Do not include guidelines that are already enforced by linters, type checkers, or that any attentive reader would naturally catch. Good candidates are:
- Subtle correctness issues (race conditions, exception paths, ordering invariants)
- Project-specific conventions not visible from a single file
- Design decisions that only make sense with broader context (e.g. why a particular abstraction is preferred)
- Patterns that look correct but have hidden runtime consequences

**Guidelines must be generic and universally applicable**

Every guideline written to a `.md` file must be phrased as a rule that applies across the entire codebase — not tied to a specific file, function, class, or one-off situation. Before writing a guideline, ask: *"Would this rule help Claude avoid a mistake in a completely different file or context?"* If the answer is no, skip it.

Concrete tests for generality — a guideline **must pass all three**:
1. The rule can be stated without naming any specific file, module, function, or variable from the codebase.
2. The rule would prevent the same class of mistake if encountered in a different part of the codebase.
3. A developer unfamiliar with the specific PR would find the rule useful on their first day.

**Explicitly skip** a thread if it falls into any of these categories:
- Purely stylistic nitpicks already covered by a formatter or linter
- One-off fixes specific to a single instance with no generalisable lesson
- Comments that describe *what to fix in this particular file* rather than *how to write code correctly in general*
- Praise, questions, or clarification requests with no actionable rule
- Comments that duplicate an existing guideline already in the `.git_learn/` files
- Trivial typo or whitespace corrections
- Comments posted by AI copilots (e.g. GitHub Copilot, Cursor, CodeRabbit, or any bot reviewer), **unless** there is a clear positive signal that the author read and incorporated the suggestion — such as the author explicitly agreeing in a reply, the thread being marked resolved by the author, or a follow-up commit that visibly addresses the point. An AI-generated comment with no human response is not evidence of a real coding standard; treat it as noise.

For each guideline, produce:
- **title** — short imperative phrase with no file or function references (e.g. "Release locks in exception paths")
- **body** — 1–3 sentences explaining the rule and why it matters, including the non-obvious reason. Must be written in generic terms — no file names, function names, variable names, or references to the specific PR or instance that surfaced the issue.
- **code_example** — include only when a code snippet is absolutely necessary to make the guideline actionable; perform a file lookup only at that point; omit entirely when the rule is clearly understandable in prose (naming conventions, design principles, workflow rules). Any snippet must use placeholder names, not names copied verbatim from the original file.

Build the guidelines JSON spec in this shape:

```json
{
  "categories": {
    "error-handling": {
      "title": "Error Handling",
      "description": "Exception paths, resource cleanup, lock management",
      "guidelines": [
        {
          "title": "Release locks in exception paths",
          "body": "Always guarantee lock release even when the protected block raises. Use a context manager or finally clause — never rely on the happy path reaching release().",
          "tags": ["backend", "concurrency", "python"],
          "code_example": {
            "language": "python",
            "bad": "lock.acquire()\nprocess()          # if this raises, lock is never released\nlock.release()",
            "good": "with lock:\n    process()      # lock released automatically on any exit"
          }
        }
      ]
    }
  }
}
```

## Step 8 — Confirm with User

Show the user a concise preview of the proposed guidelines grouped by category:

```
Found N guideline(s) across M category/categories:

  error-handling  (2 guidelines)
    • Release locks in exception paths           [backend, concurrency, python]
    • Use context managers for resource cleanup  [backend, python]

  naming  (1 guideline)
    • Avoid single-character variable names outside loop counters  [code-style]

Apply these to CLAUDE.local.md and linked files? (yes / no / edit)
```

**Do not write any files until the user confirms.** If the user says no, stop. If the user says edit, apply their changes to the spec before proceeding.

## Step 9 — Apply Guidelines and Delete Results File

Pass the confirmed spec to the apply script, and pass the `results_file` path captured in Step 6 so it is deleted after a successful apply:

```bash
python3 ${CLAUDE_PLUGIN_DIR}/scripts/apply_guidelines.py \
  --guidelines-json '<CONFIRMED_JSON_SPEC>' \
  --delete-results <RESULTS_FILE_PATH>
```

The script will:
1. Create `.git_learn/<slug>.md` for each category (or append to existing)
2. Add only guidelines whose title does not already exist — never remove or overwrite existing ones
3. Include `Avoid` / `Prefer` code blocks when a `code_example` is provided
4. Add or update the link in `CLAUDE.local.md` (creating the file if absent)
5. Add `.git_learn/` and `CLAUDE.local.md` to `.gitignore`
6. Delete `.git_learn/results_*.json` — `.git_learn/commit_seen.txt` is left intact

## Step 10 — Report

After the script runs, tell the user:

- Which files were created or updated
- How many guidelines were added vs already existed
- The structure of `CLAUDE.local.md` so they understand how to use it

Explain that Claude will load only the category files relevant to the current task context — for example, loading `error-handling.md` when reviewing exception handling code, and `naming.md` when reviewing identifiers — keeping the working context lightweight.
