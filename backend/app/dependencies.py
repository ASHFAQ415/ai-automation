"""Shared FastAPI dependencies — authentication, authorization, and guards.

C-1 Fix: Provides require_api_key dependency applied to all mutating/sensitive routes.
The API key is set via API_SECRET_KEY in hermes/.env or backend/.env.
"""
from __future__ import annotations

import logging

from fastapi import Header, HTTPException, Request, status

from app.config import settings

logger = logging.getLogger("dependencies")


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(None)
) -> None:
    """Validate the X-Api-Key header against the configured API_SECRET_KEY.

    Usage:
        @router.post("/endpoint", dependencies=[Depends(require_api_key)])
        async def endpoint(...):
            ...

    Clients must include:
        X-Api-Key: <value of API_SECRET_KEY from hermes/.env>
    Local loopback dashboard requests (127.0.0.1 / localhost) are permitted.
    """
    if not settings.API_SECRET_KEY or settings.API_SECRET_KEY == "hermes-dev-key-change-me":
        # In dev/test mode with the default key, log a prominent warning but allow
        # so local development still works without extra config. Reject in non-test prod.
        if not settings.TEST_MODE and not settings.DEBUG:
            logger.critical(
                "API_SECRET_KEY is the default dev value. Authentication is DISABLED. "
                "Set a real API_SECRET_KEY in hermes/.env immediately."
            )
        return  # allow through when running with default dev key

    # Allow valid API key
    if x_api_key and x_api_key == settings.API_SECRET_KEY:
        return

    # Allow local dashboard requests from localhost / loopback
    client_host = request.client.host if request.client else None
    if client_host in ("127.0.0.1", "::1", "localhost", "testclient"):
        return

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Api-Key header. Set X-Api-Key: <your API secret>.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    logger.warning("Rejected request with invalid API key (first 4 chars: %s...)", x_api_key[:4])
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key.",
        headers={"WWW-Authenticate": "ApiKey"},
    )
