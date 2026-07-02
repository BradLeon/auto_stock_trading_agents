"""Configuration loading.

Two layers:
  * Secrets  -> environment / .env (pydantic-settings BaseSettings)
  * AppConfig -> config/*.yaml (non-secret, version-controlled)

`get_config()` returns a cached merged view. Paths can be overridden via the
ATS_CONFIG_DIR env var (useful in tests).
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .schemas.market import Ticker

REPO_ROOT = Path(__file__).resolve().parents[2]


def _config_dir() -> Path:
    return Path(os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))


# --------------------------------------------------------------------------- #
# Secrets (.env / environment)
# --------------------------------------------------------------------------- #
class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("ATS_ENV_FILE", REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""

    fred_api_key: str = ""
    finnhub_api_key: str = ""
    fmp_api_key: str = ""          # FinancialModelingPrep — earnings-call transcripts
    tavily_api_key: str = ""
    gmail_address: str = ""            # newsletter inbox (IMAP enabled, 2FA + app password)
    gmail_app_password: str = ""       # https://myaccount.google.com/apppasswords
    gmail_imap_host: str = "imap.gmail.com"
    gmail_proxy: str = ""              # e.g. socks5://127.0.0.1:7897; default: proxy env vars
    sec_edgar_user_agent: str = "ats-bot example@example.com"
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "ats-bot/0.1"

    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 11
    ibkr_account: str = ""

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_chat_id: str = ""              # group/user chat that receives approval cards (app mode)
    feishu_verification_token: str = ""   # event callback verification (app mode)
    feishu_base_url: str = "https://open.feishu.cn"  # use open.larksuite.com for Lark
    # Group custom-bot mode (one-way push webhook + URL-button approval):
    feishu_bot_webhook: str = ""          # https://open.feishu.cn/open-apis/bot/v2/hook/<id>
    feishu_bot_secret: str = ""           # optional signing secret if the bot enables 签名校验
    feishu_approve_base: str = ""         # public base URL of `ats serve` (tunnel) for buttons
    feishu_approve_secret: str = ""       # HMAC secret protecting the GET approve links
    discord_bot_token: str = ""


# --------------------------------------------------------------------------- #
# App config (yaml)
# --------------------------------------------------------------------------- #
class LLMRoleConfig(BaseModel):
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class LLMConfig(BaseModel):
    default_provider: str = "anthropic"
    default_model: str = "claude-opus-4-8"
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout_seconds: int = 120
    max_retries: int = 3
    routing: dict[str, LLMRoleConfig] = Field(default_factory=dict)

    def for_role(self, role: str) -> LLMRoleConfig:
        """Merge defaults with any per-role override."""
        override = self.routing.get(role) or LLMRoleConfig()
        return LLMRoleConfig(
            provider=override.provider or self.default_provider,
            model=override.model or self.default_model,
            temperature=override.temperature if override.temperature is not None else self.temperature,
            max_tokens=override.max_tokens or self.max_tokens,
        )


class RiskConfig(BaseModel):
    max_position_pct: float = 0.20
    max_sector_pct: float = 0.40
    max_gross_leverage: float = 1.0
    max_single_order_usd: float = 25000
    cash_floor_pct: float = 0.05


class AccountConfig(BaseModel):
    # Paper book size used for sizing until IBKR provides the live net liquidation.
    net_liquidation_usd: float = 100000.0


class ChannelConfig(BaseModel):
    kind: str = "cli"


class ScheduleConfig(BaseModel):
    enabled: bool = False
    run_at: str = "16:15"
    timezone: str = "America/New_York"


class SectorBrief(BaseModel):
    label: str = ""
    supply_chain: str = ""


class AppConfig(BaseModel):
    environment: str = "paper"
    llm: LLMConfig = Field(default_factory=LLMConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    account: AccountConfig = Field(default_factory=AccountConfig)
    channel: ChannelConfig = Field(default_factory=ChannelConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    tickers: list[Ticker] = Field(default_factory=list)
    sectors: dict[str, SectorBrief] = Field(default_factory=dict)

    @property
    def sectors_in_use(self) -> list[str]:
        return sorted({t.sector for t in self.tickers})


class Config(BaseModel):
    """Merged configuration handed to the rest of the system."""

    app: AppConfig
    secrets: Secrets

    model_config = {"arbitrary_types_allowed": True}


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@functools.lru_cache(maxsize=1)
def get_config() -> Config:
    cfg_dir = _config_dir()
    settings_raw = _load_yaml(cfg_dir / "settings.yaml")
    watchlist_raw = _load_yaml(cfg_dir / "watchlist.yaml")
    merged = {**settings_raw, **watchlist_raw}
    return Config(app=AppConfig.model_validate(merged), secrets=Secrets())


def reset_config_cache() -> None:
    """Clear the cache (tests that point ATS_CONFIG_DIR elsewhere)."""
    get_config.cache_clear()


def load_news_sources() -> dict:
    """Load config/news_sources.yaml (RSS feeds, X accounts, keyword filters)."""
    return _load_yaml(_config_dir() / "news_sources.yaml")


def load_pead_global() -> dict:
    """Load config/pead.yaml (targets, monitor switches, schedule windows)."""
    cfg = _load_yaml(_config_dir() / "pead.yaml")
    cfg.setdefault("targets", [])
    cfg.setdefault("monitor", {})
    cfg["monitor"].setdefault("enabled", True)
    cfg["monitor"].setdefault("lookback_days", 7)
    cfg["monitor"].setdefault("push_context_updates", False)
    cfg["monitor"].setdefault("materiality_threshold", 0.7)
    cfg["monitor"].setdefault("triage", {})
    t = cfg["monitor"]["triage"]
    t.setdefault("enabled", True)
    t.setdefault("min_score", 0.35)        # below: stored but kept out of the LLM context
    t.setdefault("fulltext_score", 0.65)   # at/above: fetch the article body
    t.setdefault("max_fulltext", 4)
    t.setdefault("fulltext_chars", 12000)
    cfg.setdefault("research", {})
    r = cfg["research"]
    r.setdefault("enabled", True)
    r.setdefault("lookback_days", 2)
    r.setdefault("max_articles_per_run", 8)
    r.setdefault("article_chars", 40000)
    r.setdefault("min_confidence_event", 0.6)   # >= this -> synthetic event into pead_events
    r.setdefault("push_threshold", 0.8)         # >= this -> immediate Feishu push
    cfg.setdefault("schedule", {})
    cfg["schedule"].setdefault("prep_days_before", 3)
    cfg["schedule"].setdefault("score_after", True)
    cfg.setdefault("docs_root", "")
    return cfg


def load_pead_config(symbol: str):
    """Merge config/pead/_defaults.yaml with config/pead/<SYM>.yaml -> PeadConfig."""
    from .schemas.pead import PeadConfig

    pead_dir = _config_dir() / "pead"
    base = _load_yaml(pead_dir / "_defaults.yaml")
    override = _load_yaml(pead_dir / f"{symbol.upper()}.yaml")
    merged = {**base, **override, "symbol": symbol.upper()}
    return PeadConfig.model_validate(merged)
