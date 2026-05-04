# Search patterns for finding Claude skills

Deep reference loaded only when the default keyword expansion in `scripts/find_skills.py` is not producing enough relevant hits, or when the caller needs non-default search surfaces.

## File-shape signals

Nearly every Claude skill is shipped as `SKILL.md` (sometimes `skill.md` or `Skill.md` — `gh search code --filename` is case-insensitive on the filename) with YAML frontmatter:

```yaml
---
name: <slug>
description: <one-line trigger description>
allowed-tools: <comma-separated tool list>   # Claude Code-specific, optional
license: <optional>
---
```

Strong disambiguation signals for "this is a real Claude skill, not a random file":

| Signal | Strength |
| --- | --- |
| `allowed-tools:` in frontmatter | Very strong (Claude Code-specific) |
| Both `name:` and `description:` at top level of frontmatter | Strong |
| Path contains `.claude/skills/` | Strong |
| Path contains `skills/` | Moderate |
| Repo topic includes `claude-skills` or `claude-code` | Strong |
| Repo description mentions "Claude", "skill", "agent" | Moderate |

Weak signals that cause false positives:

- `SKILL.md` as a general project doc (quant libraries, Java Gradle projects)
- `.md` files in `skills/` directories that are tutorials, not skill metadata

## gh search code qualifiers

`gh search code` uses GitHub's legacy code search. Useful patterns:

```bash
# Files named SKILL.md with a keyword in contents
gh search code <keyword> --filename SKILL.md

# Phrase match (quoted)
gh search code '"expense tracking"' --filename SKILL.md

# Disambiguate to real Claude skills by requiring allowed-tools:
gh search code "allowed-tools:" <keyword> --filename SKILL.md

# Narrow to a specific owner's skills
gh search code <keyword> --filename SKILL.md --owner <login>
```

Notes:

- Max 100 per page, hard ceiling of 1000 per query
- No date filters on code search (use repo search for recency)
- `textMatches` in the JSON contains the highlighted snippet — useful for pre-judging before fetching
- Rate limit: 30/min for code search on authenticated requests; the parallel script stays well under

## gh search repos qualifiers

Repo search supports date and topic filters that code search lacks:

```bash
# Topic-tagged skills, most recently pushed first
gh search repos --topic claude-skills --sort updated

# Recency floor
gh search repos --topic claude-skills --updated ">2026-01-01"

# Keyword + topic
gh search repos "<keyword>" --topic claude-code --sort updated
```

Topic tags actively in use (as of 2026-04):

- `claude-skills` — most common
- `claude-code` — broader (includes plugins/tools that aren't skills)
- `claude-agent-skills`
- `agent-skills`
- `claude-code-plugin` — plugin-packaged skills
- `anthropic-skills`

## Cross-platform search (Reddit, X/Twitter, blogs)

GitHub code search is popularity-biased even with `--sort=indexed`: files from repos with more stars rank higher per query. That means individual-author skills can be buried even when they match the query better. Reddit and X/Twitter surface recent announcements from solo authors before GitHub's index catches up.

WebSearch query templates:

```
"claude skill" <topic> site:reddit.com
"claude skill" <topic> site:x.com OR site:twitter.com
"SKILL.md" <topic>
"claude code skill" <topic> site:dev.to OR site:hashnode.com
```

For each result, look in the page title, description, and snippet for a `github.com/<owner>/<repo>/blob/<branch>/<path>/SKILL.md` URL. Regex to extract:

```python
re.findall(
    r"https?://github\.com/[\w.-]+/[\w.-]+/blob/[\w.-]+/[\S]+?SKILL\.md",
    text, re.IGNORECASE,
)
```

Pass extracted URLs to `find_skills.py` via `--extra-urls` so they go through the same enrichment pipeline as native GitHub hits.

## Plugin-packaged skills

Some skills ship inside a Claude Code plugin with `.claude-plugin/plugin.json`. The SKILL.md is searchable normally, but the containing repo may have multiple skills. Plugin skills typically live at `plugins/<plugin>/skills/<skill>/SKILL.md`.

Additional search: `gh search code "skills" --filename plugin.json --extension json` surfaces plugins; cross-reference their nested SKILL.md files.

## When the default search misses

Escalation ladder when the first run returns fewer than 5 genuinely relevant candidates:

1. **Expand keywords** — more synonyms, domain-adjacent terms, tool names. "Notes" → also try `obsidian`, `roam`, `bear`, `apple notes`, `markdown scratchpad`.
2. **Drop the filename constraint** — some authors use unconventional casing. Try `gh search code <keyword> --filename skill.md` (lowercase).
3. **Search by known authors** — `--owner <login>` for prolific skill authors identified from previous runs.
4. **Search repo descriptions** — `gh search repos "claude skill <keyword>" --match description,readme`.
5. **Broaden topic tags** — add `anthropic`, `agent`, `ai-assistant` as fallback topics.
6. **Widen web search** — query `"SKILL.md" <topic>` broadly without `site:` restriction, then filter results for GitHub blob URLs.

## Recency interpretation

`file_last_commit` (per-file commit date, fetched by `find_skills.py` via `/repos/{owner}/{repo}/commits?path={path}&per_page=1`) is the correct signal for "how recent is this skill itself".

`repo.pushed_at` (whole-repo last-push) is a fallback — a repo may have pushed a docs-only commit yesterday while the specific SKILL.md hasn't changed in a year. Prefer `file_last_commit` for ranking.

Note: GitHub's commits API does **not** return the "authored" date for renames or history rewrites reliably. If a skill was recently renamed or moved, `file_last_commit` may reflect the rename, not substantive updates. Acceptable limitation — rare in practice.

## Rate limit budget

A typical skill-finder run issues approximately:

- 3–8 code searches (parallel)
- 4–12 repo searches (parallel)
- 30–60 file-content fetches (enrichment)
- 30–60 per-file commit-date fetches (enrichment)
- 5–15 repo metadata fetches (deduped)

Totals around 120–170 API calls. Authenticated limits:

- Search API: 30 req/min (code and repo search are separate buckets)
- REST API: 5000 req/hour

Well within budget for one user, one query at a time. If hitting limits, the script's `--enrich-cap` can be lowered to reduce enrichment volume.

## What is deliberately NOT done

- **No popularity weighting.** Star counts are returned in the repo metadata so the caller can display them as information, but they are never used for ranking.
- **No aggregator deprioritization.** Skills from big collections (`anthropics/skills`, `obra/superpowers`, etc.) are judged on the same content-relevance criteria as solo-author skills. A great skill in a big collection is surfaced as readily as a great skill in a one-off repo.
- **No artificial boosting.** Low-star repos are not promoted. Unknown authors are not promoted. The content of each SKILL.md is the only thing that decides ranking.

This is a deliberate design choice. Any cross-platform bias (Reddit/Twitter cross-check) exists to counteract GitHub's native popularity bias in the candidate-gathering step, not to inject an opposite bias into ranking.
