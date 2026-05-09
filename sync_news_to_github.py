#!/usr/bin/env python3
"""
Sync latest news from RSS feeds into a GitHub repository as Markdown files.

Usage:
  export NEWS_REPO_URL="git@github.com:USER/REPO.git"
  # optional if using HTTPS token:
  # export NEWS_REPO_URL="https://github.com/USER/REPO.git"
  # export GITHUB_TOKEN="ghp_xxx"
  python3 sync_news_to_github.py

Dependencies:
  pip install feedparser python-dateutil
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Iterable

import feedparser
from dateutil import parser as dateparser

REPO_URL = os.getenv("NEWS_REPO_URL", "").strip()
BRANCH = os.getenv("NEWS_BRANCH", "main")
WORKDIR = pathlib.Path(os.getenv("NEWS_WORKDIR", "/tmp/news-github-sync"))
OUTPUT_DIR = os.getenv("NEWS_OUTPUT_DIR", "news")
MAX_ITEMS_PER_FEED = int(os.getenv("NEWS_MAX_ITEMS_PER_FEED", "10"))

FEEDS = [
    # International
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    # China / Asia
    "https://www.scmp.com/rss/91/feed",
    # Finance
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://www.ft.com/rss/home",
]


def run(cmd: list[str], cwd: pathlib.Path | None = None) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def repo_url_with_token(url: str) -> str:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token and url.startswith("https://github.com/"):
        return url.replace("https://", f"https://x-access-token:{token}@", 1)
    return url


def ensure_repo() -> pathlib.Path:
    if not REPO_URL:
        raise SystemExit("Missing NEWS_REPO_URL, e.g. git@github.com:USER/REPO.git")

    if WORKDIR.exists() and (WORKDIR / ".git").exists():
        run(["git", "fetch", "origin"], cwd=WORKDIR)
        run(["git", "checkout", BRANCH], cwd=WORKDIR)
        run(["git", "pull", "--ff-only", "origin", BRANCH], cwd=WORKDIR)
    else:
        if WORKDIR.exists():
            shutil.rmtree(WORKDIR)
        run(["git", "clone", "--branch", BRANCH, repo_url_with_token(REPO_URL), str(WORKDIR)])

    # Keep cron/non-interactive commits working even when global git identity is unset.
    run(["git", "config", "user.name", os.getenv("GIT_AUTHOR_NAME", "hahaTT0902")], cwd=WORKDIR)
    run(["git", "config", "user.email", os.getenv("GIT_AUTHOR_EMAIL", "104051227+hahaTT0902@users.noreply.github.com")], cwd=WORKDIR)

    return WORKDIR


def stable_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def parse_time(entry) -> dt.datetime:
    raw = entry.get("published") or entry.get("updated") or ""
    if raw:
        try:
            return dateparser.parse(raw).astimezone(dt.timezone.utc)
        except Exception:
            pass
    return dt.datetime.now(dt.timezone.utc)


def collect_news() -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()

    for feed_url in FEEDS:
        feed = feedparser.parse(feed_url)
        source = feed.feed.get("title", feed_url)
        for entry in feed.entries[:MAX_ITEMS_PER_FEED]:
            link = entry.get("link", "").strip()
            title = entry.get("title", "").strip()
            if not title or not link:
                continue
            key = link or title
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "id": stable_id(key),
                "source": source,
                "title": title,
                "link": link,
                "summary": entry.get("summary", "").strip(),
                "published": parse_time(entry),
            })

    items.sort(key=lambda x: x["published"], reverse=True)
    return items


def md_escape(s: str) -> str:
    return s.replace("\n", " ").strip()


def write_markdown(repo: pathlib.Path, items: Iterable[dict]) -> pathlib.Path:
    now = dt.datetime.now(dt.timezone.utc)
    day = now.strftime("%Y-%m-%d")
    out_dir = repo / OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{day}.md"

    sgt = now.astimezone(dt.timezone(dt.timedelta(hours=8)))
    lines = [
        f"# News Sync - {day}",
        "",
        f"Generated at: {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Singapore time: {sgt.strftime('%Y-%m-%d %H:%M SGT')}",
        "",
    ]

    for i, item in enumerate(items, 1):
        published = item["published"].strftime("%Y-%m-%d %H:%M UTC")
        lines.extend([
            f"## {i}. {md_escape(item['title'])}",
            "",
            f"- Source: {md_escape(item['source'])}",
            f"- Published: {published}",
            f"- Link: {item['link']}",
            "",
        ])
        if item["summary"]:
            lines.extend([md_escape(item["summary"]), ""])

    out_file.write_text("\n".join(lines), encoding="utf-8")
    return out_file


def commit_and_push(repo: pathlib.Path, changed_file: pathlib.Path) -> None:
    run(["git", "add", str(changed_file.relative_to(repo))], cwd=repo)

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    if not status:
        print("No changes to commit.")
        return

    now = dt.datetime.now(dt.timezone.utc)
    sgt = now.astimezone(dt.timezone(dt.timedelta(hours=8)))
    run(["git", "commit", "-m", f"news: sync {sgt.strftime('%Y-%m-%d %H:%M SGT')}"], cwd=repo)
    run(["git", "push", "origin", BRANCH], cwd=repo)


def main() -> None:
    repo = ensure_repo()
    items = collect_news()
    if not items:
        raise SystemExit("No news collected.")
    changed_file = write_markdown(repo, items)
    commit_and_push(repo, changed_file)
    print(f"Done: {changed_file}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"Command failed: {e}", file=sys.stderr)
        sys.exit(e.returncode)
