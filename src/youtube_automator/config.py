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


load_dotenv(REPO_ROOT / ".env")


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
    discord_user_token: str = ""


class RedditSource(BaseModel):
    subreddits: list[str] = Field(default_factory=list)
    top_limit: int = 25
    new_limit: int = 50


class DiscordSource(BaseModel):
    guild_id: str = ""
    channel_ids: list[str] = Field(default_factory=list)


class WebSource(BaseModel):
    patch_notes_urls: list[str] = Field(default_factory=list)
    wiki_root: str = ""


class GameSources(BaseModel):
    reddit: RedditSource = Field(default_factory=RedditSource)
    discord: DiscordSource = Field(default_factory=DiscordSource)
    web: WebSource = Field(default_factory=WebSource)


class Sponsorship(BaseModel):
    affiliate_code: str = ""
    mention_required: bool = True
    ad_segment_path: str = ""
    description_template_id: str = "default"


class YouTubeDefaults(BaseModel):
    default_category_id: str = "20"
    default_language: str = "es"
    default_audience: str = "no"
    tag_seeds: list[str] = Field(default_factory=list)


class GameConfig(BaseModel):
    display_name: str
    slug: str
    sources: GameSources = Field(default_factory=GameSources)
    sponsorship: Sponsorship = Field(default_factory=Sponsorship)
    youtube: YouTubeDefaults = Field(default_factory=YouTubeDefaults)


class ChannelSettings(BaseModel):
    language: str = "es"
    voice_style: str = ""
    style_corpus_dir: str = "data/corpus/transcripts"


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


class Settings(BaseModel):
    channel: ChannelSettings = Field(default_factory=ChannelSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    description_templates: dict[str, str] = Field(default_factory=dict)
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
