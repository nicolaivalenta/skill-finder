---
name: skill-finder
description: Discover Claude skills on GitHub matching what the user is trying to do. Use whenever the user wants to find, search for, or discover Claude Code skills — e.g. "is there a skill for X", "find me a claude skill that does Y", "look on github for skills about Z", "what skills exist for W", "show me recent skills for V". Ranks candidates purely by content relevance to the user's intent and per-file recency — no popularity or repo-size weighting. Cross-checks Reddit and X/Twitter for skills posted by individual authors that GitHub's native ranking buries under viral collections. Presents a unified top-10 list, inlines full SKILL.md content for the most promising matches, and can install a chosen skill into ~/.claude/skills/.
---

# Skill Finder

## Purpose

The word "skill" is generic, so GitHub and web search surface the same handful of viral aggregator repos regardless of the query. This skill bypasses that bias by searching on the file-shape every Claude skill shares — `SKILL.md` with YAML frontmatter containing `name:` and `description:` — then ranking on content relevance and per-file recency alone. Cross-checks Reddit and X/Twitter for individual-author skills that GitHub's default ranking buries.

## When to trigger

Trigger whenever the user wants to discover Claude skills:

- "is there a [claude] skill for ___"
- "find me a skill that ___"
- "search github for skills about ___"
- "what claude skills exist for ___"
- "show me recent skills for ___"
- "does anyone have a skill that does ___"

Do NOT trigger when the user wants to *build* a skill (use `skill-creator`) or *use* an already-installed skill.

## Ranking philosophy

This skill deliberately does **no** popularity weighting in either direction:

- Stars, forks, follower counts: ignored
- Repo size (individual skill vs 50-skill collection): ignored
- Author identity: ignored

Every candidate SKILL.md is judged individually on two axes:

1. **Content relevance** — does the `description:` and body of this specific SKILL.md describe what the user wants? (Claude's semantic judgment.)
2. **Recency** — when was this specific SKILL.md file last committed? (Not repo-level `pushed_at` — per-file commit date, which `find_skills.py` fetches.)

Relevance is primary; recency is the tiebreaker when two candidates are similarly relevant. A 2-year-old skill that nails the intent beats a 2-day-old skill that vaguely resembles it.

## Workflow

### Step 1 — Restate the intent concretely

Restate the user's goal in 1–3 first-person sentences. If the request is ambiguous in a way that would hurt recall (e.g. "find me a skill for notes" — Apple Notes? Obsidian? general markdown?), ask one clarifying question. Otherwise proceed.

### Step 2 — Build a keyword and phrase plan

From the restated goal, derive:

- **keywords**: 3–8 single tokens a skill author might put in their `description:`, including synonyms and domain-adjacent terms
- **phrases**: 2–4 multi-word phrases more specific than individual words

Cast wider than the user's literal words. Authors use varied vocabulary: "screenshot tool" might be written as `image capture`, `screen recording`, `snipping`. "Expense tracker" might be `bookkeeping`, `receipt extraction`, `tax preparation`.

### Step 3 — Run the GitHub-native search

Invoke `scripts/find_skills.py` with the keyword and phrase lists:

```bash
scripts/find_skills.py \
  --keywords "invoice,receipt,expense,bookkeeping,tax" \
  --phrases "expense tracking,receipt extraction"
```

The script runs parallel `gh search code --filename SKILL.md <query>` and `gh search repos --topic <topic>` queries, dedupes, then enriches each candidate with:

- SKILL.md frontmatter (`name`, `description`, `allowed-tools`, `license`)
- First 2000 chars of the SKILL.md body
- Repo metadata (`pushed_at`, `stargazers_count`, `topics`, `default_branch`, `fork`)
- **Per-file last-commit date** — the ISO date of the most recent commit touching that specific SKILL.md

Output is a single JSON document with all candidates in one unified list (no bucketing). Pipe large output to a file and read selectively.

### Step 4 — Cross-check Reddit and X/Twitter

Run WebSearch in parallel with Step 3 to surface skills posted by individual authors that GitHub's default search ranking deprioritizes. Example queries:

- `"claude skill" <topic> site:reddit.com`
- `"claude skill" <topic> site:x.com OR site:twitter.com`
- `"SKILL.md" <topic>` (catches blog posts and lesser-known sharing sites)

For each result containing a `github.com/.../blob/.../SKILL.md` URL, collect the URL. Extract with a regex from the page titles and descriptions.

Re-invoke `find_skills.py` with these URLs via `--extra-urls`:

```bash
scripts/find_skills.py \
  --keywords "..." --phrases "..." \
  --extra-urls "https://github.com/alice/foo/blob/main/bar/SKILL.md,https://github.com/bob/baz/blob/main/qux/SKILL.md"
```

The script normalizes URL-sourced candidates into the same shape and enriches them the same way, so they sit alongside GitHub-native hits in the unified list.

Steps 3 and 4 can be combined into a single `find_skills.py` run if Reddit/Twitter results are gathered first.

### Step 5 — Judge each candidate on its own

Read the enriched JSON and evaluate each candidate on content relevance alone. Discard false positives silently (a finance library's `SKILL.md` that mentions "calendar" for trading days, a README that happened to use the filename).

Signals that a candidate is a real Claude skill (versus an unrelated `SKILL.md`):

- Frontmatter has both `name:` and `description:` that resemble skill metadata
- `allowed-tools:` field (Claude Code-specific, very strong signal)
- Path contains `.claude/skills/` or `skills/`
- Repo topics include `claude-skills`, `claude-code`, `agent-skills`

If fewer than 5 truly relevant candidates emerge, expand the keyword/phrase list and re-run. Search is cheap; give the query another pass before giving up.

### Step 6 — Present the unified top 10

Rank by content relevance (primary) then `file_last_commit` (tiebreaker). Present the top 10 as one single list — no "indies vs collections" sections, no visual hierarchy by popularity.

For each result:

- **Skill name** (from frontmatter)
- **What it does** — rewritten from the frontmatter description in plain English, including why this skill matches the user's query
- **Source** — `owner/repo` with clickable link to the SKILL.md
- **Last updated** — relative date from `file_last_commit` (e.g. "3 days ago", "2 months ago")

If the result count is under 10, present what's available. If there are fewer than 3 good matches, say so explicitly — don't pad the list with marginal hits.

### Step 7 — Inline the top 3–5

For the 3–5 candidates judged most relevant, display their full `SKILL.md` content inline. Fetch fresh via `gh api /repos/{owner}/{repo}/contents/{path}` if the body exceeded `find_skills.py`'s 2000-char preview. Render each inside a fenced block with the source URL as the heading so the user can read each skill in full without clicking away.

### Step 8 — Offer to install

After presenting results, ask: *"Want me to install any of these into `~/.claude/skills/`?"*

If the user picks one, invoke `scripts/install_skill.py` with the SKILL.md blob URL:

```bash
scripts/install_skill.py https://github.com/owner/repo/blob/main/path/to/SKILL.md
```

The script parses the URL, lists the skill's parent directory recursively via the GitHub contents API, downloads every file into `~/.claude/skills/<skill-name>/`, and preserves the executable bit on files in `scripts/`.

If `~/.claude/skills/<skill-name>/` already exists, the script refuses by default and prints the options (`--force` to overwrite, `--name X` to install under a different directory name). Never pass `--force` without confirming with the user.

Never install a skill the user hasn't explicitly chosen.

### Step 9 — Nothing-good-turned-up fallback

If no candidates are genuinely relevant after two keyword expansions:

- Say so directly — do not bluff a marginal hit
- List the keyword and phrase variants tried so the user can suggest more
- Offer to broaden to adjacent domains, or suggest building the skill with `skill-creator`

## Dependencies

- `gh` CLI, authenticated (`gh auth status` to verify)
- Python 3 (stdlib only)
- WebSearch tool access for the Reddit/Twitter cross-check step

## See also

- `references/search_patterns.md` — detailed query patterns, fallback strategies, rate-limit budget
