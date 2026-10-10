"""Phase 9 Verification Test Suite: Legal, Licensing, Audit Log & SBOM Inspection.

Per Phase 9 / Section 14A.6 of Master Specification:
- Cryptographically hash-chained immutable audit log
- Tamper detection in audit trail
- Software Bill of Materials (SBOM) and open-source license inspection
- Copyleft detection and compliance
- Commercial readiness manifests
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, init_db
from app.models import AuditEntry
from app.services.audit_logger import (
    GENESIS_HASH,
    log_audit_event,
    verify_audit_integrity,
)
from app.services.sbom_inspector import (
    generate_cyclonedx_sbom,
    inspect_project_licenses,
    parse_requirements_file,
)


@pytest.mark.asyncio
async def test_audit_logger_hash_chaining():
    """Verify audit log events are cryptographically chained and verifiable."""
    await init_db()
    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM audit_log"))
        await db.commit()
        unique_actor = f"op_{uuid.uuid4().hex[:6]}"

        # 1. Log sequential audit events
        e1 = await log_audit_event(
            db=db,
            actor_type="OPERATOR",
            actor_id=unique_actor,
            action="QUOTE_ISSUED",
            target_type="LEAD",
            target_id="101",
            before_state={"status": "PLAN_APPROVED"},
            after_state={"status": "QUOTE_SENT", "quote_number": "QUOTE-2026-00101"},
        )
        assert len(e1.entry_hash) == 64

        e2 = await log_audit_event(
            db=db,
            actor_type="GATEWAY",
            actor_id="razorpay_event_999",
            action="PAYMENT_RECEIVED",
            target_type="PAYMENT",
            target_id="101",
            before_state={"status": "PENDING"},
            after_state={"status": "PAID", "amount": 25000},
        )
        assert e2.previous_hash == e1.entry_hash

        e3 = await log_audit_event(
            db=db,
            actor_type="OPERATOR",
            actor_id=unique_actor,
            action="HUMAN_SIGN_OFF",
            target_type="GOAL_PLAN",
            target_id="101",
            before_state={"status": "DEMO_READY"},
            after_state={"status": "APPROVED_FOR_DELIVERY"},
        )
        assert e3.previous_hash == e2.entry_hash

        # 2. Verify complete chain integrity
        integrity = await verify_audit_integrity(db)
        assert integrity["valid"] is True
        assert integrity["total_records"] >= 3
        assert len(integrity["corruptions"]) == 0


@pytest.mark.asyncio
async def test_audit_integrity_tamper_detection():
    """Verify out-of-band record tampering is detected by cryptographic verification."""
    await init_db()
    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM audit_log"))
        await db.commit()
        unique_actor = f"tamper_{uuid.uuid4().hex[:6]}"

        t1 = await log_audit_event(
            db=db,
            actor_type="OPERATOR",
            actor_id=unique_actor,
            action="BUILD_STARTED",
            target_type="BUILD",
            target_id="202",
        )
        t2 = await log_audit_event(
            db=db,
            actor_type="OPERATOR",
            actor_id=unique_actor,
            action="BUILD_VERIFIED",
            target_type="BUILD",
            target_id="202",
        )

        # Intentionally tamper with t1's action in the database
        t1.action = "TAMPERED_ACTION_MALICIOUS"
        await db.commit()

        # Run integrity verification
        integrity = await verify_audit_integrity(db)
        assert integrity["valid"] is False
        assert len(integrity["corruptions"]) > 0
        corrupted_ids = [c["entry_id"] for c in integrity["corruptions"]]
        assert t1.id in corrupted_ids or t2.id in corrupted_ids

        # Clean up tampered rows so subsequent tests run on a clean ledger
        await db.execute(text("DELETE FROM audit_log"))
        await db.commit()


def test_sbom_inspector_and_cyclonedx():
    """Verify SBOM inspection classifies licenses and detects copyleft risk."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # 1. Permissive requirements
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("fastapi>=0.115.0\nuvicorn>=0.32.0\npydantic>=2.10.0\n", encoding="utf-8")

        inspection = inspect_project_licenses(tmp_path)
        assert inspection["compliant"] is True
        assert inspection["total_dependencies"] == 3
        assert len(inspection["copyleft_warnings"]) == 0

        # 2. Add copyleft dependency (e.g. GPL library)
        req_file.write_text("fastapi>=0.115.0\ngpl-licensed-tool==1.0.0\n", encoding="utf-8")
        from app.services.sbom_inspector import KNOWN_PACKAGE_LICENSES
        KNOWN_PACKAGE_LICENSES["gpl-licensed-tool"] = {"license": "GPL-3.0", "category": "Copyleft"}

        inspection2 = inspect_project_licenses(tmp_path)
        assert inspection2["compliant"] is False
        assert len(inspection2["copyleft_warnings"]) == 1
        assert "GPL" in inspection2["copyleft_warnings"][0]["license"]

        # 3. CycloneDX SBOM generation
        cyclone = generate_cyclonedx_sbom(tmp_path, app_name="Academy Portal")
        assert cyclone["bomFormat"] == "CycloneDX"
        assert cyclone["specVersion"] == "1.5"
        assert len(cyclone["components"]) == 2


def test_commercial_readiness_and_license_manifests():
    """Verify COMMERCIAL_READINESS.md and LICENSE_INVENTORY.md exist and contain required sections."""
    root_dir = Path(__file__).resolve().parent.parent.parent

    comm_file = root_dir / "COMMERCIAL_READINESS.md"
    assert comm_file.exists(), "COMMERCIAL_READINESS.md must exist in root"
    comm_text = comm_file.read_text(encoding="utf-8")
    assert "Terms of Service" in comm_text
    assert "STATEMENT OF WORK" in comm_text
    assert "Advance Payment" in comm_text
    assert "Refund & Cancellation" in comm_text
    assert "Intellectual Property" in comm_text
    assert "GST" in comm_text

    lic_file = root_dir / "LICENSE_INVENTORY.md"
    assert lic_file.exists(), "LICENSE_INVENTORY.md must exist in root"
    lic_text = lic_file.read_text(encoding="utf-8")
    assert "FastAPI" in lic_text
    assert "Permissive" in lic_text
    assert "CycloneDX" in lic_text
