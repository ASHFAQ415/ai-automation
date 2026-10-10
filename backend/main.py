"""Main FastAPI application entry point — Hermes AI Automation Platform v2.0.
Lifespan orchestrates: DB init, Telegram bot, HealthMonitor, FollowUpAgent,
WhatsApp webhook, and all API routes.
"""
from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routers import (
    leads_router, approvals_router, projects_router,
    stats_router, graph_router, whatsapp_router, payments_router,
    observability_router,
)
from app.services.notifier import register_ws_client, unregister_ws_client
from app.services.telegram_bot import start_telegram_bot, stop_telegram_bot
from app.services.health_monitor import start_health_monitor, stop_health_monitor
from app.services.followup_agent import start_followup_agent, stop_followup_agent
from app.services.observability import setup_logging, ObservabilityMiddleware

# H-5 Fix: Rate limiting via SlowAPI to prevent lead/webhook flooding
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address
    _SLOWAPI_AVAILABLE = True
except ImportError:
    _SLOWAPI_AVAILABLE = False
    logger_pre = logging.getLogger("server")
    logger_pre.warning("slowapi not installed — rate limiting DISABLED. Run: pip install slowapi")

setup_logging()
logger = logging.getLogger("server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("=" * 55)
    logger.info("  Hermes AI Automation Platform v%s — Starting Up", settings.APP_VERSION)
    logger.info("=" * 55)

    # 1. Initialize DB (create tables if not exist)
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized.")

    # 2. Ensure projects output directory exists
    settings.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Projects output directory: %s", settings.PROJECTS_DIR)

    # 3. Start Hermes Telegram Bot (@Preetikaa_bot — Authorized: %s)
    if settings.TELEGRAM_BOT_TOKEN:
        start_telegram_bot()
        logger.info("Hermes Telegram Bot polling started — Authorized user: %s", settings.TELEGRAM_CHAT_ID)
    else:
        logger.warning("Telegram bot DISABLED — TELEGRAM_BOT_TOKEN not set in hermes/.env")

    # 4. Start SystemHealthMonitorAgent (every 5 min)
    start_health_monitor()
    logger.info("SystemHealthMonitorAgent started (interval: 5 min)")

    # 5. Start ClientFollowUpAgent (every 30 min)
    start_followup_agent()
    logger.info("ClientFollowUpAgent started (interval: 30 min)")

    # 6. Log Hermes engine configuration
    logger.info("Primary AI: Ollama %s at %s", settings.OLLAMA_MODEL, settings.OLLAMA_BASE_URL)
    logger.info("Fallback AI: Groq %s (%s)", settings.GROQ_MODEL, "ENABLED" if settings.GROQ_API_KEY else "DISABLED")
    logger.info("Discord: %s | WhatsApp: %s | Email: %s",
                "ENABLED" if settings.DISCORD_BOT_TOKEN else "DISABLED",
                "ENABLED" if settings.WHATSAPP_ENABLED else "DISABLED",
                "ENABLED" if settings.EMAIL_ADDRESS else "DISABLED")

    # ── L-1: Startup Credential Audit ────────────────────────────────────────
    warn_count = 0
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("[CRED-AUDIT] TELEGRAM_BOT_TOKEN not set — Telegram bot DISABLED")
        warn_count += 1
    if not settings.GROQ_API_KEY:
        logger.warning("[CRED-AUDIT] GROQ_API_KEY not set — Groq Cloud fallback DISABLED")
        warn_count += 1
    if settings.API_SECRET_KEY == "hermes-dev-key-change-me":
        logger.warning(
            "[CRED-AUDIT] API_SECRET_KEY is the default dev value! "
            "Set a strong secret in hermes/.env (API_SECRET_KEY=...) before exposing to network."
        )
        warn_count += 1
    if warn_count == 0:
        logger.info("[CRED-AUDIT] ✅ All critical credentials present")

    logger.info("=" * 55)
    logger.info("  ✅ Hermes Platform ready: http://%s:%s", settings.HOST, settings.PORT)
    logger.info("  📖 API Docs: http://%s:%s/docs", settings.HOST, settings.PORT)
    logger.info("=" * 55)

    yield  # ← Application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Shutting down Hermes Platform...")
    if settings.TELEGRAM_BOT_TOKEN:
        await stop_telegram_bot()
    await stop_health_monitor()
    await stop_followup_agent()
    logger.info("Hermes Platform shutdown complete.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Autonomous AI Lead-to-Sales & Project Builder Platform — "
        "Powered by Hermes (Qwen3:4b on RTX 3050 + Groq Cloud fallback)"
    ),
    lifespan=lifespan,
)

# CORS — C-2 Fix: wildcard + credentials is a spec violation; use explicit origin list
_cors_origins_raw = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000,http://localhost:8080")
CORS_ORIGINS = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Telemetry & Request Observability (Phase 8)
app.add_middleware(ObservabilityMiddleware)

# H-5 Fix: Attach rate limiter to app state
if _SLOWAPI_AVAILABLE:
    limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    logger.info("[RATE-LIMIT] SlowAPI active: 200 req/min default per IP")

# ── API Routers ───────────────────────────────────────────────────────────────
app.include_router(leads_router)
app.include_router(approvals_router)
app.include_router(projects_router)
app.include_router(stats_router)
app.include_router(graph_router)
app.include_router(whatsapp_router)
app.include_router(payments_router)
app.include_router(observability_router)


# ── WebSocket: Live Dashboard Notifications ───────────────────────────────────
@app.websocket("/ws/notifications")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    register_ws_client(websocket)
    try:
        while True:
            await websocket.receive_text()   # keep alive
    except WebSocketDisconnect:
        unregister_ws_client(websocket)
    except Exception:
        unregister_ws_client(websocket)


# ── Health Probes: Liveness & Readiness (Phase 7 / Section 14A.5) ─────────────
@app.get("/api/health", tags=["System"])
async def health_check():
    """General health check returning operational summary."""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "ollama_model": settings.OLLAMA_MODEL,
        "groq_enabled": bool(settings.GROQ_API_KEY),
        "telegram_enabled": bool(settings.TELEGRAM_BOT_TOKEN),
        "discord_enabled": bool(settings.DISCORD_BOT_TOKEN),
        "whatsapp_enabled": settings.WHATSAPP_ENABLED,
    }


@app.get("/api/auth/token", tags=["System"])
async def get_client_auth_token(request: Request):
    """Provide API secret key for local dashboard sessions."""
    client_host = request.client.host if request.client else None
    if client_host in ("127.0.0.1", "::1", "localhost", "testclient"):
        return {"api_key": settings.API_SECRET_KEY}
    return {"api_key": ""}


@app.get("/api/health/live", tags=["System"])
async def liveness_probe():
    """Liveness probe: verifies process is alive (PROCESS_RUNNING)."""
    return {"status": "PROCESS_RUNNING", "version": settings.APP_VERSION}


@app.get("/api/health/ready", tags=["System"])
async def readiness_probe():
    """Readiness probe: verifies APPLICATION_HEALTHY (Database, WAL, Disk, Services)."""
    from fastapi.responses import JSONResponse
    from sqlalchemy import text
    from app.database import engine

    checks = {
        "database": False,
        "wal_mode": False,
        "projects_storage": False,
    }

    # 1. Check Database connection & WAL mode
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            checks["database"] = True

            wal_res = await conn.execute(text("PRAGMA journal_mode"))
            mode = wal_res.scalar()
            checks["wal_mode"] = str(mode).upper() == "WAL"
    except Exception as e:
        logger.error("Readiness check database error: %s", e)

    # 2. Check Projects storage writeability
    try:
        test_file = settings.PROJECTS_DIR / ".ready_check"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        checks["projects_storage"] = True
    except Exception as e:
        logger.error("Readiness check storage error: %s", e)

    is_healthy = checks["database"] and checks["projects_storage"]
    status_code = 200 if is_healthy else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "APPLICATION_HEALTHY" if is_healthy else "APPLICATION_DEGRADED",
            "checks": checks,
            "version": settings.APP_VERSION,
        }
    )


# ── Hermes Brain Hub — Command Center ────────────────────────────────────────
HERMES_BRAIN_HTML = Path(__file__).resolve().parent.parent / "frontend" / "brain_hub.html"

@app.get("/brain", tags=["Hermes Brain"])
async def hermes_brain_hub():
    """Serve the Hermes Brain Hub Command Center UI."""
    from fastapi.responses import HTMLResponse
    if HERMES_BRAIN_HTML.exists():
        return HTMLResponse(content=HERMES_BRAIN_HTML.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Hermes Brain Hub UI not found</h1>", status_code=404)


# ── Static Frontend (served last — after all API routes) ─────────────────────
STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
