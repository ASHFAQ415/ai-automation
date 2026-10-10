"""Phase 7 Verification Test Suite: Process Supervision, Health Probes, Crash Recovery.

Per Section 10 and 14A.5 of Master Specification:
- Distinction between PROCESS_RUNNING (live) and APPLICATION_HEALTHY (ready)
- Database WAL verification during readiness check
- Process crash resilience and restart verification
"""
from __future__ import annotations

import time
import pytest
from starlette.testclient import TestClient

from app.database import init_db
from main import app
from scripts.supervise_hermes import HermesProcessSupervisor


@pytest.mark.asyncio
async def test_liveness_and_readiness_probes():
    await init_db()

    client = TestClient(app, raise_server_exceptions=False)

    # 1. Liveness probe (PROCESS_RUNNING)
    live_resp = client.get("/api/health/live")
    assert live_resp.status_code == 200
    live_data = live_resp.json()
    assert live_data["status"] == "PROCESS_RUNNING"
    assert "version" in live_data

    # 2. Readiness probe (APPLICATION_HEALTHY)
    ready_resp = client.get("/api/health/ready")
    assert ready_resp.status_code == 200
    ready_data = ready_resp.json()
    assert ready_data["status"] == "APPLICATION_HEALTHY"
    assert ready_data["checks"]["database"] is True
    assert ready_data["checks"]["wal_mode"] is True
    assert ready_data["checks"]["projects_storage"] is True


def test_supervisor_crash_restart_simulation():
    # Use dedicated test port
    test_port = 8899
    supervisor = HermesProcessSupervisor(port=test_port, host="127.0.0.1", poll_interval=1.0)

    try:
        # 1. Start process
        proc1 = supervisor.start_process()
        pid1 = proc1.pid
        assert proc1.poll() is None

        # Give process time to bind port
        time.sleep(2.0)

        # 2. Check health
        healthy, msg = supervisor.check_health()
        assert healthy is True
        assert msg == "APPLICATION_HEALTHY"

        # 3. Simulate sudden crash / process kill
        proc1.kill()
        proc1.wait(timeout=2)
        assert proc1.poll() is not None

        # 4. Supervisor restarts process
        supervisor.restart_count += 1
        proc2 = supervisor.start_process()
        pid2 = proc2.pid
        assert pid2 != pid1
        assert proc2.poll() is None

        time.sleep(2.0)

        # 5. Confirm newly restarted process is healthy
        healthy_after, msg_after = supervisor.check_health()
        assert healthy_after is True
        assert msg_after == "APPLICATION_HEALTHY"

    finally:
        supervisor.stop_process()
