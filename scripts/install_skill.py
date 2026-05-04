#!/usr/bin/env python3
"""
install_skill.py — clone a Claude skill from GitHub into ~/.claude/skills/.

Usage:
    install_skill.py <github-skill-md-url> [--name <override>] [--force]

Expects a github.com blob URL pointing at the SKILL.md, e.g.:
    https://github.com/alice/my-skills/blob/main/code-reviewer/SKILL.md

Walks the skill's parent directory recursively via the GitHub contents API,
downloads every file to ~/.claude/skills/<skill-name>/, and preserves the
executable bit on scripts so they can be run without chmod.

Dependencies: python3 (stdlib only), `gh` CLI authenticated.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

GITHUB_BLOB_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$"
)
SAFE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def parse_url(url: str) -> tuple[str, str, str, str, str]:
    m = GITHUB_BLOB_RE.match(url.strip())
    if not m:
        raise SystemExit(f"error: not a github.com blob URL: {url}")
    owner, repo, branch, path = m.groups()
    if not path.lower().endswith("skill.md"):
        raise SystemExit(f"error: URL must point at a SKILL.md (got path: {path})")
    parts = path.split("/")
    if len(parts) < 2:
        raise SystemExit(f"error: SKILL.md must be inside a directory (got path: {path})")
    skill_dir = "/".join(parts[:-1])
    skill_name = parts[-2]
    return owner, repo, branch, skill_dir, skill_name


def gh_api(endpoint: str) -> list | dict:
    r = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise SystemExit(f"error: gh api {endpoint} failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def list_files(owner: str, repo: str, branch: str, path: str) -> list[tuple[str, str]]:
    endpoint = f"/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(branch)}"
    items = gh_api(endpoint)
    if isinstance(items, dict):
        items = [items]
    files: list[tuple[str, str]] = []
    for item in items:
        if item["type"] == "file":
            files.append((item["path"], item["download_url"]))
        elif item["type"] == "dir":
            files.extend(list_files(owner, repo, branch, item["path"]))
    return files


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "skill-finder-installer"})
    with urllib.request.urlopen(req, timeout=30) as r:
        dest.write_bytes(r.read())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="GitHub blob URL to the SKILL.md")
    ap.add_argument("--name", help="Override installed skill directory name")
    ap.add_argument("--force", action="store_true", help="Overwrite existing directory")
    args = ap.parse_args()

    owner, repo, branch, skill_dir, default_name = parse_url(args.url)
    skill_name = args.name or default_name
    if not SAFE_NAME_RE.match(skill_name):
        skill_name = re.sub(r"[^a-z0-9_-]+", "-", skill_name.lower()).strip("-")
        if not skill_name:
            raise SystemExit(f"error: could not derive a safe skill name from {default_name}")

    dest = Path.home() / ".claude" / "skills" / skill_name
    if dest.exists():
        if not args.force:
            print(
                f"error: {dest} already exists. Pass --force to overwrite, "
                f"or --name to install under a different name.",
                file=sys.stderr,
            )
            return 1
        shutil.rmtree(dest)

    print(f"installing {owner}/{repo}@{branch}:{skill_dir} -> {dest}")
    files = list_files(owner, repo, branch, skill_dir)
    if not files:
        print(f"error: no files at {skill_dir}", file=sys.stderr)
        return 1

    prefix = skill_dir + "/"
    for remote_path, download_url in files:
        rel_path = remote_path[len(prefix):] if remote_path.startswith(prefix) else remote_path
        local_path = dest / rel_path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"  {rel_path}")
        download(download_url, local_path)

    scripts_dir = dest / "scripts"
    if scripts_dir.is_dir():
        for f in scripts_dir.rglob("*"):
            if f.is_file() and f.suffix in {".py", ".sh"}:
                f.chmod(0o755)

    print(f"\ninstalled: {dest}")
    print(f"reload skills in Claude Code to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
