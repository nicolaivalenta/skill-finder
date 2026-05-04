#!/usr/bin/env python3
"""
find_skills.py — discover Claude skills on GitHub matching a user intent.

Gathers a candidate pool of SKILL.md files without popularity weighting,
enriches each with its frontmatter and a per-file last-commit date, and
returns one unified JSON list. The caller (Claude inside the skill-finder
skill) ranks the list on content relevance and recency alone.

Strategy:
  1. Run many code-search queries in parallel: `gh search code --filename SKILL.md <kw>`
  2. Run repo-search queries scoped to Claude-skill topics for secondary discovery
  3. Optionally ingest extra SKILL.md URLs supplied from web-search results
     (Reddit, X/Twitter, blogs) — the caller harvests these separately
  4. Dedupe
  5. Enrich every candidate: fetch raw SKILL.md text (parse frontmatter),
     fetch repo metadata, fetch the last commit touching that specific file
  6. Emit JSON to stdout

No bucketing, no popularity weighting, no hidden filters. Every hit is
presented equally for the caller to judge.

Dependencies: python3 (stdlib only), `gh` CLI authenticated.

Typical invocation:
    scripts/find_skills.py \\
        --keywords "invoice,receipt,tax" \\
        --phrases "expense tracking,bookkeeping" \\
        --extra-urls "https://github.com/alice/foo/blob/main/bar/SKILL.md"
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import re
import subprocess
import sys
import urllib.parse

GITHUB_BLOB_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+?SKILL\.md)/?$",
    re.IGNORECASE,
)


def _run(args: list[str], timeout: int = 60) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return ""
        return r.stdout
    except subprocess.TimeoutExpired:
        return ""


def search_code(query: str, filename: str = "SKILL.md", limit: int = 50) -> list[dict]:
    out = _run([
        "gh", "search", "code",
        "--filename", filename,
        "--limit", str(limit),
        "--json", "path,repository,textMatches,url",
        query,
    ])
    try:
        return json.loads(out or "[]")
    except json.JSONDecodeError:
        return []


def search_repos(query: str | None, topic: str | None, limit: int = 20) -> list[dict]:
    args = [
        "gh", "search", "repos",
        "--sort", "updated",
        "--limit", str(limit),
        "--json", "name,fullName,description,pushedAt,stargazersCount,owner,url",
    ]
    if topic:
        args += ["--topic", topic]
    if query:
        args.append(query)
    out = _run(args)
    try:
        return json.loads(out or "[]")
    except json.JSONDecodeError:
        return []


def fetch_file_text(owner: str, repo: str, path: str) -> str:
    out = _run([
        "gh", "api", f"/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}",
        "--jq", ".content // empty",
    ], timeout=30)
    if not out.strip():
        return ""
    try:
        return base64.b64decode(out.strip()).decode("utf-8", errors="replace")
    except Exception:
        return ""


def fetch_repo_meta(owner: str, repo: str) -> dict:
    out = _run([
        "gh", "api", f"/repos/{owner}/{repo}",
        "--jq", "{pushed_at, stargazers_count, description, topics, html_url, default_branch, fork}",
    ], timeout=30)
    try:
        return json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        return {}


def fetch_file_last_commit(owner: str, repo: str, path: str) -> str:
    """Date of the most recent commit touching this specific file."""
    out = _run([
        "gh", "api",
        f"/repos/{owner}/{repo}/commits?path={urllib.parse.quote(path)}&per_page=1",
        "--jq", ".[0].commit.committer.date // empty",
    ], timeout=30)
    return out.strip()


_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> dict:
    m = _FM.match(text or "")
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def normalize_code_hit(raw: dict, query_tag: str) -> dict:
    return {
        "nameWithOwner": raw["repository"]["nameWithOwner"],
        "path": raw["path"],
        "url": raw["url"],
        "fragments": [t.get("fragment", "") for t in (raw.get("textMatches") or [])][:3],
        "matched_query": query_tag,
    }


def parse_blob_url(url: str) -> dict | None:
    m = GITHUB_BLOB_RE.match(url.strip())
    if not m:
        return None
    owner, repo, _branch, path = m.groups()
    return {
        "nameWithOwner": f"{owner}/{repo}",
        "path": path,
        "url": url,
        "fragments": [],
        "matched_query": "extra-url",
    }


def dedupe(hits: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for h in hits:
        key = (h["nameWithOwner"], h["path"])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keywords", default="", help="Comma-separated single words.")
    ap.add_argument("--phrases", default="", help="Comma-separated multi-word phrases.")
    ap.add_argument(
        "--extra-urls", default="",
        help="Comma-separated github.com blob URLs of SKILL.md files harvested from web search.",
    )
    ap.add_argument("--limit", type=int, default=50, help="Max hits per code query (default 50).")
    ap.add_argument("--repo-limit", type=int, default=20, help="Max hits per repo query.")
    ap.add_argument("--enrich-cap", type=int, default=60, help="Max candidates to enrich (default 60).")
    ap.add_argument("--no-enrich", action="store_true", help="Skip enrichment (faster, less useful).")
    args = ap.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    phrases = [p.strip() for p in args.phrases.split(",") if p.strip()]
    extra_urls = [u.strip() for u in args.extra_urls.split(",") if u.strip()]

    if not keywords and not phrases and not extra_urls:
        print(json.dumps({"error": "provide --keywords, --phrases, or --extra-urls"}))
        return 2

    code_queries: list[tuple[str, str]] = []
    for kw in keywords:
        code_queries.append((kw, f"kw:{kw}"))
    for ph in phrases:
        code_queries.append((f'"{ph}"', f"ph:{ph}"))

    repo_queries: list[tuple[str | None, str | None, str]] = []
    for topic in ("claude-skills", "claude-code", "claude-agent-skills", "agent-skills"):
        repo_queries.append((None, topic, f"topic:{topic}"))
        for kw in keywords:
            repo_queries.append((kw, topic, f"topic:{topic} kw:{kw}"))
    for kw in keywords:
        repo_queries.append((f"claude skill {kw}", None, f"repo-kw:{kw}"))

    code_hits_raw: list[tuple[dict, str]] = []
    repo_hits: list[dict] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        code_futs = {
            ex.submit(search_code, q, "SKILL.md", args.limit): label
            for q, label in code_queries
        }
        repo_futs = [
            ex.submit(search_repos, q, t, args.repo_limit)
            for q, t, _label in repo_queries
        ]
        for fut in concurrent.futures.as_completed(code_futs):
            label = code_futs[fut]
            for r in fut.result():
                code_hits_raw.append((r, label))
        for fut in concurrent.futures.as_completed(repo_futs):
            repo_hits.extend(fut.result())

    hits: list[dict] = [normalize_code_hit(r, label) for r, label in code_hits_raw]
    for url in extra_urls:
        parsed = parse_blob_url(url)
        if parsed:
            hits.append(parsed)

    hits = dedupe(hits)

    seen_repos: set[str] = set()
    repo_hits_out: list[dict] = []
    for h in repo_hits:
        full = h.get("fullName", "")
        if full and full not in seen_repos:
            seen_repos.add(full)
            repo_hits_out.append(h)
    repo_hits_out.sort(key=lambda r: r.get("pushedAt") or "", reverse=True)

    if not args.no_enrich:
        to_enrich = hits[: args.enrich_cap]
        repos_needed = {h["nameWithOwner"] for h in to_enrich}

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            meta_futs = {
                nwo: ex.submit(fetch_repo_meta, *nwo.split("/", 1))
                for nwo in repos_needed
            }
            file_futs: dict[tuple[str, str], concurrent.futures.Future] = {}
            commit_futs: dict[tuple[str, str], concurrent.futures.Future] = {}
            for h in to_enrich:
                owner, repo = h["nameWithOwner"].split("/", 1)
                key = (h["nameWithOwner"], h["path"])
                file_futs[key] = ex.submit(fetch_file_text, owner, repo, h["path"])
                commit_futs[key] = ex.submit(fetch_file_last_commit, owner, repo, h["path"])
            for h in to_enrich:
                key = (h["nameWithOwner"], h["path"])
                text = file_futs[key].result()
                fm = parse_frontmatter(text)
                h["frontmatter"] = {
                    "name": fm.get("name", ""),
                    "description": fm.get("description", ""),
                    "allowed_tools": fm.get("allowed-tools", ""),
                    "license": fm.get("license", ""),
                }
                h["body_preview"] = (text[: 2000] if text else "")
                h["file_last_commit"] = commit_futs[key].result()
                h["repo"] = meta_futs[h["nameWithOwner"]].result()

        hits.sort(key=lambda h: h.get("file_last_commit") or "", reverse=True)

    json.dump({
        "query": {"keywords": keywords, "phrases": phrases, "extra_urls": extra_urls},
        "counts": {
            "total_candidates": len(hits),
            "enriched": min(len(hits), args.enrich_cap) if not args.no_enrich else 0,
            "repo_hits": len(repo_hits_out),
        },
        "candidates": hits,
        "related_repos": repo_hits_out,
    }, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
