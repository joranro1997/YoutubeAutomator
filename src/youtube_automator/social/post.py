"""Discord webhook + Twitter (X) posters. Both gracefully skip when the
relevant credential is empty (so the user can enable just one if they
don't have Twitter API access yet).
"""

from __future__ import annotations

import httpx

from ..config import get_env


def post_discord(message: str) -> str:
    """POST a message to the configured Discord webhook. Returns the URL
    of the channel message on success (or '' if no webhook configured)."""
    url = get_env().discord_webhook_url
    if not url:
        return ""
    resp = httpx.post(url, json={"content": message}, timeout=15.0)
    resp.raise_for_status()
    # Discord returns 204 (no body) by default; ?wait=true would yield JSON.
    return url


def post_twitter(message: str) -> str:
    """Post a tweet via the X API v2. Returns the tweet URL on success
    (or '' if credentials are missing — silently skipped)."""
    env = get_env()
    if not (env.twitter_consumer_key and env.twitter_consumer_secret
            and env.twitter_access_token and env.twitter_access_token_secret):
        return ""
    import tweepy
    client = tweepy.Client(
        consumer_key=env.twitter_consumer_key,
        consumer_secret=env.twitter_consumer_secret,
        access_token=env.twitter_access_token,
        access_token_secret=env.twitter_access_token_secret,
    )
    resp = client.create_tweet(text=message)
    tweet_id = resp.data["id"]                       # type: ignore[index]
    # Username isn't returned; configured channel.links.twitter has the handle
    # but for now build a generic intent-style URL by id (works without handle).
    return f"https://twitter.com/i/web/status/{tweet_id}"
