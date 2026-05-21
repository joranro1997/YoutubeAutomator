"""Social queue + companion-post builder (pure, no network)."""

from datetime import datetime, timedelta, timezone

from youtube_automator.social.queue import (
    SocialPost,
    SocialQueue,
    build_companion_posts,
)


def test_build_companion_posts_makes_one_per_channel():
    when = datetime(2026, 5, 20, 16, 30, tzinfo=timezone.utc)
    posts = build_companion_posts(
        game_slug="lom", video_slug="demo",
        video_url="https://youtu.be/abc", title="INSANE Mushroom Strategy",
        tags=["lom", "mushroom rpg", "lom guide"], post_at=when,
    )
    assert len(posts) == 2
    channels = sorted(p.channel for p in posts)
    assert channels == ["discord", "twitter"]
    # The title is the catchphrase; both bodies include the video URL.
    for p in posts:
        assert "INSANE Mushroom Strategy" in p.content
        assert "youtu.be/abc" in p.content
    # Twitter must fit in 280 chars and carry hashtags.
    tw = next(p for p in posts if p.channel == "twitter")
    assert len(tw.content) <= 280
    assert "#lom" in tw.content
    # space-containing tags become single-word hashtags
    assert "#mushroomrpg" in tw.content


def test_twitter_truncates_long_title():
    when = datetime(2026, 5, 20, 16, 30, tzinfo=timezone.utc)
    long_title = "X" * 400
    posts = build_companion_posts(
        game_slug="lom", video_slug="demo",
        video_url="https://youtu.be/abc", title=long_title,
        tags=["lom"], post_at=when,
    )
    tw = next(p for p in posts if p.channel == "twitter")
    assert len(tw.content) <= 280


def test_queue_roundtrip(tmp_path):
    p = tmp_path / "queue.json"
    q = SocialQueue()
    q.add(SocialPost(
        game="lom", video_slug="v", channel="discord",
        content="hi", post_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
    ))
    q.save(p)
    again = SocialQueue.load(p)
    assert len(again.posts) == 1
    assert again.posts[0].content == "hi"


def test_due_filters_pending_and_past():
    now = datetime(2026, 5, 20, 18, tzinfo=timezone.utc)
    q = SocialQueue()
    q.add(SocialPost(game="g", video_slug="a", channel="discord",
                     content="x", post_at=now - timedelta(minutes=1)))
    q.add(SocialPost(game="g", video_slug="b", channel="discord",
                     content="y", post_at=now + timedelta(minutes=1)))
    q.add(SocialPost(game="g", video_slug="c", channel="discord",
                     content="z", post_at=now - timedelta(minutes=5),
                     status="posted"))
    due = q.due(now)
    assert len(due) == 1
    assert due[0].video_slug == "a"
