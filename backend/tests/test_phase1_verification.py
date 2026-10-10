"""Phase 1 Verification Test Suite: Acceptance gates, Layer A & B tests, security scanning, checksums.

Per Section 4 and 14A.3 of Master Specification:
- Deterministic Layer A domain acceptance tests (School clash detection, Generic CRUD)
- Independent Layer B specification tests (Contract validation, error handling)
- Checksum calculation & gate enforcement
"""
from __future__ import annotations

import tempfile
from pathlib import Path
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.services.acceptance_tests import (
    run_deterministic_acceptance_tests,
    run_independent_acceptance_tests,
)
from app.services.build_verifier import (
    calculate_directory_checksum,
    check_compilation,
    check_dependencies,
    check_secrets,
    check_security_ast,
    run_full_build_verification,
)


class SchoolSlotIn(BaseModel):
    day: str
    period: int
    class_group: str
    subject: str
    teacher: str
    room: str
    color_type: str | None = None


@pytest.fixture
def mock_school_app():
    """Create a FastAPI app that conforms to the School Timetable specification."""
    app = FastAPI(title="School Timetable Pro", version="1.0.0")

    db = [
        {"id": 1, "day": "Monday", "period": 1, "class_group": "Grade 10-A", "subject": "Math", "teacher": "Dr. Vance", "room": "Room 302"},
    ]
    next_id = 2

    @app.get("/api/health")
    def health():
        return {"status": "online", "scheduled_periods": len(db)}

    @app.get("/api/timetable")
    def get_timetable():
        return db

    @app.get("/api/conflicts")
    def conflicts():
        return {"status": "CLEAR", "conflict_count": 0, "conflicts": []}

    @app.post("/api/timetable")
    def add_slot(slot: SchoolSlotIn):
        nonlocal next_id
        for s in db:
            if s["day"].lower() == slot.day.lower() and s["period"] == slot.period:
                if s["teacher"].lower() == slot.teacher.lower():
                    raise HTTPException(status_code=400, detail="Teacher clash")
                if s["room"].lower() == slot.room.lower():
                    raise HTTPException(status_code=400, detail="Room clash")

        rec = {"id": next_id, **slot.model_dump()}
        next_id += 1
        db.append(rec)
        return rec

    @app.delete("/api/timetable/{slot_id}")
    def delete_slot(slot_id: int):
        nonlocal db
        db = [s for s in db if s["id"] != slot_id]
        return {"status": "deleted", "id": slot_id}

    @app.get("/")
    def home():
        return {"message": "Timetable UI"}

    return app


def test_dependency_verification():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        # 1. Missing requirements.txt
        res = check_dependencies(tmp_path)
        assert res["passed"] is False

        # 2. Valid requirements.txt
        (tmp_path / "requirements.txt").write_text("fastapi>=0.115.0\nuvicorn>=0.32.0\n", encoding="utf-8")
        res = check_dependencies(tmp_path)
        assert res["passed"] is True
        assert len(res["dependencies"]) == 2


def test_compilation_verification():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        # Valid python code
        (tmp_path / "valid.py").write_text("x = 10\ndef foo(): return x * 2\n", encoding="utf-8")
        assert check_compilation(tmp_path)["passed"] is True

        # Invalid syntax
        (tmp_path / "broken.py").write_text("def broken_syntax( {", encoding="utf-8")
        assert check_compilation(tmp_path)["passed"] is False


def test_secret_scanning_in_project():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        # Clean file
        (tmp_path / "clean.py").write_text("APP_ENV = 'production'\n", encoding="utf-8")
        assert check_secrets(tmp_path)["passed"] is True

        # Leaked Telegram token
        (tmp_path / "leaky.py").write_text("BOT_TOKEN = 'bot123456789:ABCdefGHIjklMNOpqrSTUvwxYZ123456789'\n", encoding="utf-8")
        assert check_secrets(tmp_path)["passed"] is False


def test_security_ast_check():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        # Safe code
        (tmp_path / "safe.py").write_text("def calc(a, b): return a + b\n", encoding="utf-8")
        assert check_security_ast(tmp_path)["passed"] is True

        # Unsafe eval
        (tmp_path / "unsafe.py").write_text("user_expr = '2+2'; result = eval(user_expr)\n", encoding="utf-8")
        assert check_security_ast(tmp_path)["passed"] is False


def test_layer_a_deterministic_school_tests(mock_school_app):
    report = run_deterministic_acceptance_tests(
        mock_school_app,
        requirement="Build a high school class schedule and timetable system",
        title="School Timetable Pro",
    )
    print("DEBUG REPORT:", report)
    assert report["success"] is True
    assert report["domain"] == "school"
    assert report["passed_tests"] == report["total_tests"]


def test_layer_b_independent_tests(mock_school_app):
    plan = {"title": "School Timetable Pro", "core_features": ["Timetable", "Clash Detection"]}
    report = run_independent_acceptance_tests(mock_school_app, plan)
    assert report["success"] is True
    assert report["layer"] == "LAYER_B_INDEPENDENT"
    assert report["passed_tests"] == report["total_tests"]


def test_full_build_verification_gate():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        (tmp_path / "requirements.txt").write_text("fastapi>=0.115.0\nuvicorn>=0.32.0\n", encoding="utf-8")
        (tmp_path / "main.py").write_text(
            """from fastapi import FastAPI
app = FastAPI()
_db = []
@app.get('/api/health')
def health(): return {'status': 'online'}
@app.get('/api/items')
def get_items(): return _db
@app.post('/api/items')
def create(item: dict):
    _db.append(item)
    return item
@app.delete('/api/items/{item_id}')
def delete(item_id: int): return {'deleted': item_id}
@app.get('/')
def home(): return {'message': 'Home'}
""",
            encoding="utf-8",
        )

        checksum = calculate_directory_checksum(tmp_path)
        assert len(checksum) == 64  # Valid SHA-256 hex string

        report = run_full_build_verification(
            project_dir=tmp_path,
            requirement="Generic workflow automation",
            title="Workflow App",
        )
        assert report["gate_passed"] is True
        assert report["checksum"] == checksum
        assert report["total_gates_evaluated"] == 13

        # Assert all 13 gates executed and passed in order
        expected_gates = [
            "gate_1_dependencies",
            "gate_2_compilation",
            "gate_3_secret_quarantine",
            "gate_4_security_ast",
            "gate_5_layer_a_domain",
            "gate_6_layer_b_contract",
            "gate_7_static_frontend",
            "gate_8_port_conflict",
            "gate_9_domain_risk",
            "gate_10_vulnerability_scan",
            "gate_11_cyclonedx_sbom",
            "gate_12_checksum_integrity",
            "gate_13_audit_ledger",
        ]
        for g in expected_gates:
            assert g in report["gates"], f"Gate {g} missing from verification report"
            assert report["gates"][g].get("passed") is True, f"Gate {g} failed: {report['gates'][g]}"

