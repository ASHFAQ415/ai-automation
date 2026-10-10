"""Invoice & Quotation System with Configurable Tax Fields and PDF Generation.

Implements Phase 6.2 and Section 14A.6 of Master Specification:
- Business identifiers: QUOTE-2026-XXXXX, INV-2026-XXXXX
- Configurable commercial fields (business name, address, tax registration, client details)
- Professional PDF invoice and quotation generation via ReportLab
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Any

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

logger = logging.getLogger("invoice_service")

# Default agency commercial configuration
DEFAULT_AGENCY_CONFIG = {
    "business_name": "Mohd Ashfaq Enterprise — Hermes Platform",
    "address": "Hyderabad, Telangana, India",
    "tax_id": "GSTIN-36AABCH1234F1Z5",
    "email": "contact@ashfaq.dev",
    "currency": "INR",
    "currency_symbol": "₹",
}


def generate_quote_number(lead_id: int, year: int | None = None) -> str:
    """Format immutable quote identifier: QUOTE-2026-00001."""
    y = year or datetime.now(timezone.utc).year
    return f"QUOTE-{y}-{lead_id:05d}"


def generate_invoice_number(lead_id: int, year: int | None = None) -> str:
    """Format immutable invoice identifier: INV-2026-00001."""
    y = year or datetime.now(timezone.utc).year
    return f"INV-{y}-{lead_id:05d}"


def create_invoice_pdf(
    doc_number: str,
    client_name: str,
    client_email: str,
    client_company: str | None,
    line_items: list[dict[str, Any]],
    tax_rate: float = 0.18,  # e.g. 18% GST
    advance_paid: float = 0.0,
    agency_config: dict[str, str] | None = None,
    is_quote: bool = False,
) -> bytes:
    """Render a publication-quality PDF invoice or quotation and return bytes."""
    cfg = {**DEFAULT_AGENCY_CONFIG, **(agency_config or {})}
    sym = cfg.get("currency_symbol", "₹")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#1e293b"),
    )
    meta_style = ParagraphStyle(
        "MetaText",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#475569"),
    )
    bold_meta = ParagraphStyle(
        "BoldMeta",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#0f172a"),
    )

    story = []

    # 1. Header banner
    doc_type = "COMMERCIAL QUOTATION" if is_quote else "TAX INVOICE"
    header_data = [
        [
            Paragraph(f"<b>{cfg['business_name']}</b><br/>{cfg['address']}<br/>Tax ID: {cfg['tax_id']}<br/>{cfg['email']}", meta_style),
            Paragraph(f"<b>{doc_type}</b><br/><b>Ref:</b> {doc_number}<br/><b>Date:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d')}<br/><b>Status:</b> {'PROPOSED' if is_quote else 'ISSUED'}", bold_meta),
        ]
    ]
    header_table = Table(header_data, colWidths=[300, 240])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 20))

    # 2. Bill-To section
    client_info = f"<b>Bill To:</b><br/><b>{client_name}</b><br/>"
    if client_company:
        client_info += f"{client_company}<br/>"
    client_info += f"Email: {client_email}"
    story.append(Paragraph(client_info, meta_style))
    story.append(Spacer(1, 15))

    # 3. Line items table
    table_rows = [["Item Description", "Qty", "Unit Price", "Total"]]

    subtotal = 0.0
    for item in line_items:
        desc = item.get("description", "Software Engineering Deliverable")
        qty = item.get("quantity", 1)
        price = float(item.get("unit_price", 0.0))
        item_total = qty * price
        subtotal += item_total
        table_rows.append([desc, str(qty), f"{sym}{price:,.2f}", f"{sym}{item_total:,.2f}"])

    tax_amount = subtotal * tax_rate
    grand_total = subtotal + tax_amount
    balance_due = max(0.0, grand_total - advance_paid)

    table_rows.append(["", "", "Subtotal:", f"{sym}{subtotal:,.2f}"])
    if tax_rate > 0:
        table_rows.append(["", "", f"Tax ({(tax_rate*100):.0f}%):", f"{sym}{tax_amount:,.2f}"])
    table_rows.append(["", "", "Grand Total:", f"{sym}{grand_total:,.2f}"])
    if advance_paid > 0:
        table_rows.append(["", "", "Advance Paid:", f"{sym}{advance_paid:,.2f}"])
        table_rows.append(["", "", "Balance Due:", f"{sym}{balance_due:,.2f}"])

    items_table = Table(table_rows, colWidths=[260, 50, 110, 120])
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, len(line_items)), 0.5, colors.HexColor("#e2e8f0")),
        ("LINEBELOW", (2, -len(table_rows) + len(line_items) + 1), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("FONTNAME", (2, -3), (-1, -1), "Helvetica-Bold"),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 25))

    # 4. Terms and conditions footer
    terms = (
        "<b>Terms & Conditions:</b><br/>"
        "1. Deliverables are governed by Hermes commercial terms of service.<br/>"
        "2. Payment terms: Advance required prior to autonomous build execution.<br/>"
        "3. Final handover is contingent upon acceptance testing verification."
    )
    story.append(Paragraph(terms, meta_style))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
