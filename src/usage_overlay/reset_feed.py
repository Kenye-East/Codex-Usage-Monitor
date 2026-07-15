from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import re
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


NITTER_BASE_URL = "https://nitter.net"
CODEX_SOURCE_ACCOUNT = "thsottiaux"
RESET_PATTERN = re.compile(r"\breset\w*\b", re.IGNORECASE)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ResetPost:
    provider: str
    title: str
    content: str
    url: str
    published_at: datetime


def unread_posts(posts: list[ResetPost], read_urls: set[str] | tuple[str, ...]) -> list[ResetPost]:
    read = set(read_urls)
    return [post for post in posts if post.url not in read]


def feed_url(username: str) -> str:
    return f"{NITTER_BASE_URL}/{username}/rss"


class NitterResetFeed:
    """Loads public Nitter feeds for the two usage-monitoring sources."""

    def __init__(self, fetcher: Callable[[str], str] | None = None) -> None:
        self._fetcher = fetcher or _download

    def fetch(self, now: datetime | None = None) -> list[ResetPost]:
        return parse_reset_feed(self._fetcher(feed_url(CODEX_SOURCE_ACCOUNT)), "codex", now)


def parse_reset_feed(feed_xml: str, provider: str, now: datetime | None = None) -> list[ResetPost]:
    """Return reset-related Nitter RSS items published in the last seven days."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=7)
    root = ET.fromstring(feed_xml)
    posts: list[ResetPost] = []

    for item in root.findall("./channel/item"):
        title = _clean(item.findtext("title"))
        content = _clean(item.findtext("description"))
        published_at = _parse_date(item.findtext("pubDate"))
        url = _original_post_url((item.findtext("link") or "").strip())
        if not title or not url or published_at is None or published_at < cutoff:
            continue
        if not RESET_PATTERN.search(f"{title} {content}"):
            continue
        posts.append(ResetPost(provider, title, content, url, published_at))

    return sorted(posts, key=lambda post: post.published_at, reverse=True)


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", unescape(HTML_TAG_PATTERN.sub(" ", value or ""))).strip()


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _original_post_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "nitter.net":
        return url
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) == 3 and parts[1] == "status":
        return f"https://x.com/{parts[0]}/status/{parts[2]}"
    return url


def _download(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Codex-Usage-Monitor/0.1"})
    with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed public RSS endpoint
        return response.read().decode("utf-8")
