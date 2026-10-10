"""Phase 2 Verification Test Suite: Sandbox, Port Allocation, Secret Quarantine, Watchdog.

Per Section 5 and Phase 2 of Master Specification:
- Dynamic collision-free port allocation (8081-8999)
- Strict secret isolation (no agency credentials in client runtime)
- Sandbox process management, health check, and watchdog cleanup
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
import httpx
import pytest

from app.services.sandbox_manager import (
    PortAllocator,
    SecretSanitizer,
    sandbox_manager,
)


def test_dynamic_port_allocator():
    allocator = PortAllocator(start=8800, end=8810)

    # 1. Allocate for project 101 and 102
    port1 = allocator.allocate(101)
    port2 = allocator.allocate(102)

    assert port1 != port2
    assert 8800 <= port1 <= 8810
    assert 8800 <= port2 <= 8810

    # 2. Check allocation registry
    assert allocator.get_port(101) == port1
    assert allocator.get_port(102) == port2

    # 3. Release port 101
    allocator.release(101)
    assert allocator.get_port(101) is None

    # 4. Re-allocating for another project can reuse released port
    port3 = allocator.allocate(103)
    assert port3 in [port1, port2 + 1]


def test_secret_sanitizer_quarantine():
    # 1. Generate isolated env
    env = SecretSanitizer.get_isolated_env("Client Suite", 8085)

    # Verify runtime vars are set
    assert env["DEMO_MODE"] == "true"
    assert env["PROJECT_NAME"] == "Client Suite"
    assert env["PORT"] == "8085"

    # Verify NO agency secrets are present
    disallowed = ["TELEGRAM", "GROQ", "RAZORPAY", "STRIPE", "DISCORD", "DATABASE"]
    for k in env:
        for d in disallowed:
            assert d not in k.upper(), f"Found agency secret key {k} in isolated env!"

    # 2. Test that assert_no_agency_secrets_in_env catches leaks
    leaked_env = {"DEMO_MODE": "true", "TELEGRAM_BOT_TOKEN": "secret_token_123"}
    with pytest.raises(AssertionError, match="Agency secret key"):
        SecretSanitizer.assert_no_agency_secrets_in_env(leaked_env)


def test_sandbox_process_lifecycle_and_watchdog():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        (tmp_path / "main.py").write_text(
            """import os
from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get('/api/health')
def health():
    return {
        'status': 'online',
        'demo_mode': os.getenv('DEMO_MODE'),
        'has_agency_secret': 'TELEGRAM_BOT_TOKEN' in os.environ
    }
""",
            encoding="utf-8",
        )

        project_id = 9999
        # Start sandboxed process
        info = sandbox_manager.start_demo_process(
            project_id=project_id,
            project_dir=tmp_path,
            project_name="Isolated Sandbox Test",
            timeout_seconds=30,
        )

        port = info["port"]
        pid = info["pid"]
        assert info["status"] == "RUNNING"
        assert port >= 8081

        # Wait up to 5 seconds for uvicorn to bind
        time.sleep(1.5)

        # Verify live responsiveness via httpx
        url = f"http://127.0.0.1:{port}/api/health"
        try:
            with httpx.Client(timeout=4.0) as client:
                resp = client.get(url)
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "online"
                assert data["demo_mode"] == "true"
                assert data["has_agency_secret"] is False
        finally:
            # Stop demo process cleanly
            stopped = sandbox_manager.stop_demo_process(project_id)
            assert stopped is True

        # Confirm watchdog cleans any remaining
        cleaned = sandbox_manager.cleanup_all_orphans()
        assert cleaned == 0  # Already stopped cleanly

