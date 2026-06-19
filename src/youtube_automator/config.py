"""Config loader.

Reads `config/settings.yaml` and `config/games.yaml`, validates with Pydantic,
and exposes typed accessors. Env vars (loaded from `.env`) override secrets.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .paths import CONFIG_DIR, REPO_ROOT


# Load .env. We pass override=True because:
#   (1) Shells sometimes export the key with an EMPTY value (e.g. `export
#       ANTHROPIC_API_KEY=` left in a profile), and the default behavior of
#       load_dotenv would leave that empty value untouched.
#   (2) For this project the .env file is the source of truth on a given
#       machine — explicitly set env vars in CI/cron can still win because
#       this only runs once at import time.
load_dotenv(REPO_ROOT / ".env", override=True)


class Env(BaseSettings):
    """Secrets and per-machine env, loaded from .env / environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    youtube_client_secrets_path: str = "./secrets/client_secret.json"
    youtube_token_path: str = "./secrets/youtube_token.json"
    youtube_channel_id: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "YoutubeAutomator/0.1"
    discord_bot_token: str = ""
    # Discord webhook URL for the auto-post companion message (per video).
    # Get from Discord: server settings -> Integrations -> Webhooks -> New.
    discord_webhook_url: str = ""
    # Twitter / X v2 API credentials (OAuth 1.0a User Context — required to
    # post tweets). Leave empty to skip Twitter posting silently.
    twitter_consumer_key: str = ""
    twitter_consumer_secret: str = ""
    twitter_access_token: str = ""
    twitter_access_token_secret: str = ""


class RedditSource(BaseModel):
    subreddits: list[str] = Field(default_factory=list)
    top_limit: int = 25
    new_limit: int = 50


class DiscordGuild(BaseModel):
    guild_id: str
    label: str = ""
    channel_ids: list[str] = Field(default_factory=list)


class MirrorChannel(BaseModel):
    """A channel in the USER's own server that mirrors an upstream announcement
    channel via Discord's native 'Follow' feature. The bot reads from these.
    """

    channel_id: str
    label: str = ""
    # How many recent messages to fetch on each `yta research` run.
    fetch_limit: int = 50


class DiscordSource(BaseModel):
    # A game may have content in multiple Discords (e.g. game's own + Aptoide's
    # cross-game announcements). Kept as reference data — the BOT reads from
    # mirror_channels, not from these.
    guilds: list[DiscordGuild] = Field(default_factory=list)
    # Channels in the user's own server (id below) where upstream announcement
    # channels are mirrored via Follow. The bot reads from these.
    mirror_channels: list[MirrorChannel] = Field(default_factory=list)


class WebSource(BaseModel):
    patch_notes_urls: list[str] = Field(default_factory=list)
    wiki_root: str = ""


class YouTubeSource(BaseModel):
    """YouTube search-based trending signal for a game (no API key — uses yt-dlp)."""

    search_queries: list[str] = Field(default_factory=list)
    results_per_query: int = 15
    max_age_days: int = 30


class GameSources(BaseModel):
    reddit: RedditSource = Field(default_factory=RedditSource)
    discord: DiscordSource = Field(default_factory=DiscordSource)
    web: WebSource = Field(default_factory=WebSource)
    youtube: YouTubeSource = Field(default_factory=YouTubeSource)


class Sponsorship(BaseModel):
    affiliate_code: str = ""
    mention_required: bool = True
    ad_segment_path: str = ""
    description_template_id: str = "default"
    # Aptoide affiliate short links for this game.
    download_link: str = ""        # e.g. http://aptoi.de/MidwayLoM
    browser_play_link: str = ""    # e.g. http://aptoi.de/all-platforms-LoM (LoM only)
    # An evergreen "how to use the affiliate code" video, linked in every description.
    promo_video_url: str = ""
    # Public invite URL to the game's OWN community Discord (not Aptoide's),
    # surfaced in the description's "Join Our Communities" block. e.g.
    # "https://discord.gg/lom". Empty -> the description renderer falls back
    # to a slug-based guess for the original two games.
    official_discord_invite: str = ""


class YouTubeDefaults(BaseModel):
    default_category_id: str = "20"
    default_language: str = "es"
    default_audience: str = "no"
    tag_seeds: list[str] = Field(default_factory=list)
    # ID of the YouTube playlist this game's videos should be added to.
    # Get it from the playlist URL: youtube.com/playlist?list=PL... → the
    # PL... portion. Empty -> skip the playlist step.
    playlist_id: str = ""


class SilenceCut(BaseModel):
    """Phase 3 — pause/silence trimming thresholds (applied outside Premiere).

    Defaults tuned for energetic gaming voice-over: don't clip word tails,
    don't leave dead air. Override per game if a title needs different pacing.
    """

    # "edges"   -> trim ONLY leading/trailing dead air per fragment; keep the
    #              take's natural flow (internal pauses untouched). Default.
    # "internal"-> also cut long internal silences (aggressive jump-cut;
    #              produced 168 pieces on a single take — usually too much).
    mode: str = "edges"
    threshold_db: float = -35.0
    min_silence_sec: float = 0.4
    # Padding kept around speech so cuts don't feel choppy / clip syllables.
    keep_margin_sec: float = 0.12
    # Never emit a kept span shorter than this (avoids stutter cuts).
    min_keep_sec: float = 0.30


class PromoBlock(BaseModel):
    """The pre-recorded Aptoide segment, treated as a rigid block.

    In the LoM template the promo spans V7 (continuous video) + the gameplay
    audio track, where the audio has a deliberate ~0.4s internal excision at
    the spoken affiliate code. That internal structure must be preserved
    verbatim regardless of where the block lands on the timeline.
    """

    present: bool = False
    # Case-insensitive substring identifying the promo clips in the template.
    clip_name_contains: str = "PROMO"
    # Source asset under assets/aptoide_ads/ (e.g. "lom.mp4"). "" = none yet.
    asset_filename: str = ""
    # Aim to drop the promo this far into the video ("relatively near start").
    target_offset_sec: float = 67.0
    # How to land the insertion point so it never splits a sentence.
    #   silence -> split the take at the natural pause nearest the target
    #              (one cut only; correct for whole-take recording). Default.
    #   exact   -> split exactly at target_offset_sec (may cut mid-speech).
    snap: str = "silence"


class PremiereTemplate(BaseModel):
    """Phase 3 — per-game Premiere template profile.

    Track roles are declared here (the LoM/LoE templates assign the same
    roles to DIFFERENT tracks — e.g. gameplay audio is A1 in LoM but A2 in
    LoE — so nothing about track numbers can be hardcoded in code). The
    declared roles are validated at runtime against a describe-dump of the
    actual .prproj before any timeline surgery.

    Track labels are 1-based UI labels ("V7", "A1"); code maps them to the
    0-based ExtendScript track arrays.
    """

    # Defaults to "<slug>.prproj" under premiere_templates_dir() when "".
    template_filename: str = ""
    # "" = operate on the active sequence.
    sequence_name: str = ""
    # The track where recorded gameplay (and, if present, the promo) lives.
    content_video_track: str = "V7"
    # The audio track that mirrors content_video_track (the voice-over).
    gameplay_audio_track: str = "A1"
    # The continuous background-music track (videoplayback.mp3).
    music_track: str = "A2"
    # Single full-length clips that only need stretching to final duration.
    static_decor_video_tracks: list[str] = Field(default_factory=list)
    # Overlays keyed to the promo: 3 phases when promo present, else 1 clip.
    overlay_tracks: list[str] = Field(default_factory=list)
    # Intro elements (like/sub/bell) pinned to the start — never touched.
    fixed_intro_video_tracks: list[str] = Field(default_factory=list)
    fixed_intro_audio_tracks: list[str] = Field(default_factory=list)
    # Hidden/empty tracks to leave entirely alone.
    ignore_video_tracks: list[str] = Field(default_factory=list)
    ignore_audio_tracks: list[str] = Field(default_factory=list)
    promo: PromoBlock = Field(default_factory=PromoBlock)
    silence: SilenceCut = Field(default_factory=SilenceCut)


class PhotoshopTemplate(BaseModel):
    """Per-game thumbnail rendering settings.

    Templates are auto-discovered from <photoshop_templates_dir>/<slug>/
    (alphabetical order = rotation order). Each .psd has 2 text Smart
    Objects at the top of the layer stack (after THUMBNAIL_DARK_01): the
    first is the top word, the second is the bottom phrase. The renderer
    edits the text inside those two Smart Objects and exports a PNG.
    """

    width: int = 1280
    height: int = 720
    # How the thumbnail_copy from metadata.json is split into top + bottom.
    #   first_space -> first word top, rest bottom (default; matches the
    #                  channel's 2-word style: "BEGINNER GUIDE", etc.)
    #   newline     -> split on the first '\n'.
    split_strategy: str = "first_space"

    # --- text auto-fit (anti-overflow) ----------------------------------- #
    # Long thumbnail copy used to get CLIPPED by the size of the Smart Object
    # that holds it. When autofit is on, the renderer measures the text vs the
    # SO's own canvas and, if it overflows, shrinks the font (and re-centres it
    # WITHIN the SO, so the SO's place on the thumbnail is untouched) until it
    # fits with `text_fit_margin` padding — never below `text_fit_min_scale` of
    # the template's designed size. If it still can't fit at that floor, the
    # pipeline shortens the text and re-renders once, then warns.
    autofit_text: bool = True
    # padding each side, as a fraction of the design box (small => text fills more)
    text_fit_margin: float = Field(0.03, ge=0.0, lt=0.5)
    # never shrink below this fraction of the designed size
    text_fit_min_scale: float = Field(0.35, gt=0.0, le=1.0)


class GameConfig(BaseModel):
    display_name: str
    slug: str
    sources: GameSources = Field(default_factory=GameSources)
    sponsorship: Sponsorship = Field(default_factory=Sponsorship)
    youtube: YouTubeDefaults = Field(default_factory=YouTubeDefaults)
    premiere_template: PremiereTemplate = Field(default_factory=PremiereTemplate)
    photoshop_template: PhotoshopTemplate = Field(default_factory=PhotoshopTemplate)


class ChannelLinks(BaseModel):
    """Permanent social/community links surfaced in every description."""
    twitch: str = ""
    twitter: str = ""
    discord: str = ""


class ChannelSettings(BaseModel):
    language: str = "en"
    voice_style: str = ""
    style_corpus_dir: str = "data/corpus/transcripts"
    creator_handle: str = ""
    creator_bio: str = ""
    links: ChannelLinks = Field(default_factory=ChannelLinks)


class LLMSettings(BaseModel):
    models: dict[str, str] = Field(default_factory=dict)
    max_tokens_default: int = 4096


class PipelineSettings(BaseModel):
    videos_per_week_target: int = 2
    approval_required: bool = False
    human_in_the_loop_topic: bool = True
    human_in_the_loop_script: bool = True
    human_in_the_loop_metadata: bool = True


class ContractGuardrails(BaseModel):
    description_must_contain: list[str] = Field(default_factory=list)
    description_must_not_contain: list[str] = Field(default_factory=list)
    script_must_mention_aptoide_at_least: int = 1
    require_source_citation_for_factual_claims: bool = True


class ScheduleSettings(BaseModel):
    """When to publish uploaded videos. One video/day across BOTH games.

    Times are in the channel-local timezone (the API receives UTC; we
    convert). If `now` is past today's slot the next free day starts
    tomorrow.
    """

    timezone: str = "Europe/Madrid"
    publish_hour: int = 18
    publish_minute: int = 30


class Settings(BaseModel):
    channel: ChannelSettings = Field(default_factory=ChannelSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    description_templates: dict[str, str] = Field(default_factory=dict)
    hashtag_lines: dict[str, str] = Field(default_factory=dict)
    contract_guardrails: ContractGuardrails = Field(default_factory=ContractGuardrails)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_env() -> Env:
    return Env()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data = _load_yaml(CONFIG_DIR / "settings.yaml")
    return Settings.model_validate(data)


@lru_cache(maxsize=1)
def get_games() -> dict[str, GameConfig]:
    data = _load_yaml(CONFIG_DIR / "games.yaml")
    raw = data.get("games", {})
    return {key: GameConfig.model_validate(val) for key, val in raw.items()}


def get_game(slug_or_key: str) -> GameConfig:
    games = get_games()
    if slug_or_key in games:
        return games[slug_or_key]
    for game in games.values():
        if game.slug == slug_or_key:
            return game
    raise KeyError(f"Unknown game: {slug_or_key!r}. Known: {list(games.keys())}")
