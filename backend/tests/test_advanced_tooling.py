"""Verification Test Suite for Advanced Tooling: Trivy, Semgrep, pytest-httpx, Prometheus/OpenTelemetry, and llama.cpp.

Verifies:
- pytest-httpx mock transport integration
- Semgrep SAST configuration and AST gate alignment
- Trivy vulnerability scanner configuration
- Prometheus exposition format and OpenTelemetry W3C traceparent headers
- llama.cpp inference client and comparative inference benchmarking
"""
from __future__ import annotations

import yaml
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.services.llm_service import LLMService
from app.services.observability import generate_prometheus_metrics, metrics
from app.services.regression_benchmarks import benchmark_inference_backends
from main import app


def test_semgrep_configuration_validity():
    """Verify .semgrep.yml exists, parses as valid YAML, and covers required security checks."""
    root_dir = Path(__file__).resolve().parent.parent.parent
    semgrep_file = root_dir / ".semgrep.yml"
    assert semgrep_file.exists(), ".semgrep.yml must exist in repository root"

    content = yaml.safe_load(semgrep_file.read_text(encoding="utf-8"))
    assert "rules" in content
    rules = {r["id"]: r for r in content["rules"]}

    # Required security rule checks
    assert "dangerous-eval-ast" in rules
    assert "dangerous-subprocess-shell-true" in rules
    assert "hardcoded-secret-token" in rules
    assert "unsafe-yaml-load" in rules


def test_trivy_configuration_validity():
    """Verify trivy.yaml exists, parses as valid YAML, and defines vulnerability thresholds."""
    root_dir = Path(__file__).resolve().parent.parent.parent
    trivy_file = root_dir / "trivy.yaml"
    assert trivy_file.exists(), "trivy.yaml must exist in repository root"

    config = yaml.safe_load(trivy_file.read_text(encoding="utf-8"))
    assert "severity" in config
    assert "CRITICAL" in config["severity"]
    assert "HIGH" in config["severity"]


def test_prometheus_and_opentelemetry_headers():
    """Verify /api/metrics/prometheus returns Prometheus format and requests carry OpenTelemetry headers."""
    client = TestClient(app, raise_server_exceptions=False)

    # 1. OpenTelemetry traceparent header check
    inbound_trace = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    resp = client.get("/api/health/live", headers={"traceparent": inbound_trace})
    assert resp.status_code == 200
    assert "traceparent" in resp.headers
    assert "4bf92f3577b34da6a3ce929d0e0e4736" in resp.headers["traceparent"]
    assert resp.headers["X-Trace-ID"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert "X-Span-ID" in resp.headers

    # 2. Prometheus exposition format endpoint check
    prom_resp = client.get("/api/metrics/prometheus")
    assert prom_resp.status_code == 200
    assert "text/plain" in prom_resp.headers.get("content-type", "")
    prom_text = prom_resp.text
    assert "# HELP" in prom_text
    assert "# TYPE" in prom_text
    assert "process_uptime_seconds" in prom_text
    assert "http_requests_total" in prom_text


@pytest.mark.asyncio
async def test_pytest_httpx_mock_llama_cpp(httpx_mock):
    """Verify pytest-httpx cleanly mocks llama.cpp inference without network dependencies."""
    mock_url = "http://localhost:8001/v1/chat/completions"
    mock_response = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1720000000,
        "model": "qwen3-4b-instruct-q4_k_m.gguf",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": '{"title": "Automated Clinic POS", "status": "PLANNED", "features": ["Appointments", "Billing"]}',
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 45, "completion_tokens": 28, "total_tokens": 73},
    }

    httpx_mock.add_response(url=mock_url, method="POST", json=mock_response, status_code=200)

    result = await LLMService.query_llama_cpp(
        prompt="Design clinic management system",
        base_url="http://localhost:8001/v1",
        model="qwen3-4b-instruct-q4_k_m.gguf",
    )

    assert result is not None
    assert result["title"] == "Automated Clinic POS"
    assert "Billing" in result["features"]


@pytest.mark.asyncio
async def test_inference_backends_benchmarking():
    """Verify comparative inference benchmarking engine runs across backends."""
    bench = await benchmark_inference_backends(
        prompt="Sample benchmark prompt",
        backends=["mock"],
    )
    assert bench["benchmark"] == "HERMES_INFERENCE_ENGINE_COMPARISON"
    assert "mock" in bench["backends"]
    assert bench["backends"]["mock"]["status"] == "ONLINE"
    assert bench["backends"]["mock"]["latency_ms"] >= 0
