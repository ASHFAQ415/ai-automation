"""Configuration module for AI Automation Platform.
All credentials are loaded exclusively from hermes/.env or backend/.env.
Never hardcode secrets here — use environment variables only.
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ConfigDict, field_validator, model_validator
from pydantic_settings import BaseSettings

# ── Credential loading order ─────────────────────────────────────────────────
# 1. hermes/.env  ← shared secrets (Telegram, Discord, Groq, Email, WhatsApp)
# 2. backend/.env ← local overrides
BASE_DIR = Path(__file__).resolve().parent.parent.parent
HERMES_ENV = BASE_DIR / "hermes" / ".env"
if HERMES_ENV.exists():
    load_dotenv(HERMES_ENV)

LOCAL_ENV = BASE_DIR / "backend" / ".env"
if LOCAL_ENV.exists():
    load_dotenv(LOCAL_ENV, override=True)


class Settings(BaseSettings):
    model_config = ConfigDict(case_sensitive=True, extra="allow")

    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "AI Lead Automation & Autonomous Project Builder"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False          # ← was True; exposes tracebacks in prod
    TEST_MODE: bool = os.getenv("TEST_MODE", "false").lower() in ("true", "1", "yes")
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # ── API Security ─────────────────────────────────────────────────────────
    # Set API_SECRET_KEY in hermes/.env or backend/.env
    API_SECRET_KEY: str = os.getenv("API_SECRET_KEY", "hermes-dev-key-change-me")

    @field_validator("API_SECRET_KEY", mode="after")
    @classmethod
    def warn_default_api_key(cls, v: str) -> str:
        """H-4 Fix: Warn loudly if the default dev key is in production use."""
        if v == "hermes-dev-key-change-me":
            warnings.warn(
                "\n" + "!" * 60 + "\n"
                "[SECURITY] API_SECRET_KEY is the default dev value.\n"
                "Set a strong secret in hermes/.env: API_SECRET_KEY=<random>\n"
                "!" * 60,
                stacklevel=2,
            )
        return v

    # ── Database ─────────────────────────────────────────────────────────────
    DATABASE_PATH: Path = BASE_DIR / "backend" / "ai_automation.db"
    # H-3 Fix: DATABASE_URL is computed from DATABASE_PATH via model_validator
    # so that overriding DATABASE_PATH in .env is correctly reflected.
    DATABASE_URL: str = ""

    @model_validator(mode="after")
    def compute_database_url(self) -> "Settings":
        """H-3 Fix: Derive DATABASE_URL from DATABASE_PATH so env overrides propagate."""
        if not self.DATABASE_URL:
            self.DATABASE_URL = f"sqlite+aiosqlite:///{self.DATABASE_PATH.as_posix()}"
        return self

    # ── Local Ollama LLM ─────────────────────────────────────────────────────
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen3:4b"
    OLLAMA_FALLBACK_MODEL: str = "qwen2.5:3b"

    @field_validator("OLLAMA_BASE_URL", mode="before")
    @classmethod
    def clean_ollama_base_url(cls, v: str) -> str:
        if isinstance(v, str):
            return v.replace("/v1", "").rstrip("/")
        return "http://localhost:11434"

    # ── Local llama.cpp Alternative Engine ───────────────────────────────────
    LLAMA_CPP_BASE_URL: str = os.getenv("LLAMA_CPP_BASE_URL", "http://localhost:8001/v1")
    LLAMA_CPP_MODEL: str = os.getenv("LLAMA_CPP_MODEL", "qwen3-4b-instruct-q4_k_m.gguf")
    LLAMA_CPP_ENABLED: bool = os.getenv("LLAMA_CPP_ENABLED", "false").lower() in ("true", "1", "yes")

    # ── Groq Cloud API ───────────────────────────────────────────────────────
    # Loaded from GROQ_API_KEY in hermes/.env
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    # ── Telegram (Hermes @Preetikaa_bot) ─────────────────────────────────────
    # Loaded from hermes/.env: TELEGRAM_BOT_TOKEN, TELEGRAM_HOME_CHANNEL
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""    # maps from TELEGRAM_HOME_CHANNEL

    @field_validator("TELEGRAM_CHAT_ID", mode="before")
    @classmethod
    def resolve_telegram_chat_id(cls, v: str) -> str:
        # Support both TELEGRAM_CHAT_ID and TELEGRAM_HOME_CHANNEL env var names
        return v or os.getenv("TELEGRAM_HOME_CHANNEL", "") or os.getenv("TELEGRAM_ALLOWED_USERS", "")

    # ── Discord ──────────────────────────────────────────────────────────────
    DISCORD_BOT_TOKEN: str = ""
    DISCORD_GUILD_ID: str = ""
    DISCORD_CHANNEL_ID: str = os.getenv("DISCORD_CHANNEL_ID", "1442350843130282106")

    # ── WhatsApp (Cloud API & Bridge) ─────────────────────────────────────────
    WHATSAPP_ENABLED: bool = True
    WHATSAPP_ALLOWED_USERS: str = ""   # loaded from hermes/.env
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_APP_SECRET: str = ""
    # H-2 Fix: Shared secret between Hermes and the Baileys bridge process
    WHATSAPP_BRIDGE_SECRET: str = os.getenv("WHATSAPP_BRIDGE_SECRET", "")

    # ── Razorpay Payment Gateway ─────────────────────────────────────────────
    # WIRING FIX: RAZORPAY_KEY_SECRET was referenced in payment_service.py
    # (generate_checkout_link, verify_razorpay_signature) but was never declared
    # in Settings — getattr() silently returned "" but raised AttributeError
    # in strict contexts. Now formally declared.
    RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")

    # ── Email (Gmail SMTP) ───────────────────────────────────────────────────
    EMAIL_ADDRESS: str = ""            # loaded from hermes/.env
    EMAIL_PASSWORD: str = ""           # loaded from hermes/.env
    EMAIL_SMTP_HOST: str = "smtp.gmail.com"
    EMAIL_SMTP_PORT: int = 587

    # ── Directories ──────────────────────────────────────────────────────────
    PROJECTS_DIR: Path = BASE_DIR / "projects"
    HERMES_DIR: Path = BASE_DIR / "hermes"


    # ── Observability & Monitoring ───────────────────────────────────────────
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "json")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

settings = Settings()
# PROJECTS_DIR is created lazily at first use, not at import time
