from datetime import datetime, timezone

from usage_overlay.reset_feed import NitterResetFeed, ResetPost, parse_reset_feed, unread_posts


def test_parse_reset_feed_keeps_recent_reset_posts_and_skips_undated_items() -> None:
    feed = """<?xml version=\"1.0\"?><rss><channel>
      <item><title>Usage reset is coming</title><link>https://x.com/a/status/1</link>
        <description>Full reset announcement</description><pubDate>Tue, 14 Jul 2026 05:00:00 GMT</pubDate></item>
      <item><title>Old reset post</title><link>https://x.com/a/status/2</link>
        <description>Too old</description><pubDate>Tue, 30 Jun 2026 05:00:00 GMT</pubDate></item>
      <item><title>Reset but no date</title><link>https://x.com/a/status/3</link><description>Skip it</description></item>
      <item><title>Product update</title><link>https://x.com/a/status/4</link>
        <description>Nothing about usage</description><pubDate>Tue, 14 Jul 2026 05:00:00 GMT</pubDate></item>
    </channel></rss>"""

    posts = parse_reset_feed(
        feed,
        provider="codex",
        now=datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc),
    )

    assert len(posts) == 1
    assert posts[0].provider == "codex"
    assert posts[0].url == "https://x.com/a/status/1"
    assert posts[0].title == "Usage reset is coming"


def test_nitter_feed_uses_the_codex_public_rss_url() -> None:
    requested: list[str] = []

    client = NitterResetFeed(lambda url: requested.append(url) or "<rss><channel /></rss>")

    assert client.fetch() == []
    assert requested == ["https://nitter.net/thsottiaux/rss"]


def test_parse_reset_feed_converts_nitter_permalink_to_original_x_post() -> None:
    feed = """<rss><channel><item><title>reset now</title>
      <link>https://nitter.net/thsottiaux/status/12345#m</link>
      <pubDate>Tue, 14 Jul 2026 05:00:00 GMT</pubDate></item></channel></rss>"""

    posts = parse_reset_feed(feed, "codex", datetime(2026, 7, 15, tzinfo=timezone.utc))

    assert posts[0].url == "https://x.com/thsottiaux/status/12345"


def test_unread_posts_excludes_previously_read_urls() -> None:
    posts = [
        ResetPost("codex", "new", "", "https://x.com/a/status/1", datetime(2026, 7, 15, tzinfo=timezone.utc)),
        ResetPost("codex", "read", "", "https://x.com/b/status/2", datetime(2026, 7, 14, tzinfo=timezone.utc)),
    ]

    assert unread_posts(posts, {"https://x.com/b/status/2"}) == [posts[0]]
