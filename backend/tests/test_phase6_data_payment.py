"""Phase 6 Verification Test Suite: Data Maturity, Invoicing, Tenant Isolation, Backups.

Per Section 9 and 14A.6 of Master Specification:
- Sequential immutable quote/invoice numbering
- Configurable PDF invoice generation (ReportLab)
- Tenant/Client data isolation
- SQLite database backup and restore verification
"""
from __future__ import annotations

import tempfile
from pathlib import Path
import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal, init_db
from app.models import Lead
from app.services.backup_service import backup_database, verify_backup_restore
from app.services.invoice_service import (
    create_invoice_pdf,
    generate_invoice_number,
    generate_quote_number,
)


def test_quote_and_invoice_numbering():
    q_num = generate_quote_number(lead_id=7, year=2026)
    assert q_num == "QUOTE-2026-00007"

    inv_num = generate_invoice_number(lead_id=42, year=2026)
    assert inv_num == "INV-2026-00042"


def test_invoice_pdf_generation():
    line_items = [
        {"description": "Autonomous AI Agent Backend Implementation", "quantity": 1, "unit_price": 35000.0},
        {"description": "Production Acceptance Verification Gate", "quantity": 1, "unit_price": 10000.0},
    ]

    pdf_bytes = create_invoice_pdf(
        doc_number="INV-2026-00001",
        client_name="Apex Logistics Ltd",
        client_email="billing@apexlogistics.io",
        client_company="Apex Global",
        line_items=line_items,
        tax_rate=0.18,
        advance_paid=20000.0,
    )

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 1000
    # PDF files must start with the %PDF- magic signature
    assert pdf_bytes.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_tenant_isolation():
    await init_db()

    async with AsyncSessionLocal() as session:
        # Create records for Tenant Alpha and Tenant Beta
        lead_alpha = Lead(name="Alpha Corp", tenant_id="tenant_alpha", requirement="Alpha Automation")
        lead_beta = Lead(name="Beta LLC", tenant_id="tenant_beta", requirement="Beta Automation")
        session.add_all([lead_alpha, lead_beta])
        await session.commit()

        # Query scoped to Tenant Alpha
        res_alpha = await session.execute(select(Lead).where(Lead.tenant_id == "tenant_alpha"))
        alpha_leads = res_alpha.scalars().all()
        assert all(l.tenant_id == "tenant_alpha" for l in alpha_leads)
        assert any(l.name == "Alpha Corp" for l in alpha_leads)
        assert not any(l.name == "Beta LLC" for l in alpha_leads)


def test_database_backup_and_restore_verification():
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "source.db"
        backup_dir = tmp_path / "backups"

        # Initialize mock DB with Hermes tables
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE leads (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE project_goal_plans (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("CREATE TABLE project_builds (id INTEGER PRIMARY KEY, status TEXT)")
        conn.execute("CREATE TABLE payment_records (id INTEGER PRIMARY KEY, amount INTEGER)")
        conn.execute("INSERT INTO leads (name) VALUES ('Test Company')")
        conn.commit()
        conn.close()

        # 1. Execute backup
        backup_file = backup_database(db_path, backup_dir)
        assert backup_file.exists()

        # 2. Execute restore verification
        report = verify_backup_restore(backup_file)
        assert report["passed"] is True
        assert "leads" in report["verified_tables"]
        assert report["leads_count"] == 1
