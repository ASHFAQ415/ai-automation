# Commercial Readiness & Legal Framework Manifest

**Author:** Mohd Ashfaq  
**Platform:** Hermes Autonomous AI Lead-to-Sales & Project Builder Engine  
**Version:** 2.0.0 (Production Release)  
**Governing Standard:** Master IDE Implementation Instruction — Section 14A.6  

---

## 1. Executive Summary

This document establishes the commercial, contractual, and regulatory framework for commercial software delivery executed by the Hermes Autonomous Engine. Every client engagement, proposal, automated quote, and delivered software asset must operate strictly under the legal instruments codified herein.

---

## 2. Standard Client Terms of Service (TOS)

### 2.1 Scope of Service
Hermes provides autonomous software engineering, scoping, rapid scaffolding, and custom software delivery. All projects are built to specifications approved in the client **Statement of Work (SOW)**.

### 2.2 Client Obligations
1. **Accurate Specifications:** The Client is responsible for reviewing and verifying the initial Project Goal Plan before authorizing the build.
2. **Acceptance Testing:** The Client agrees to test the delivered software within seven (7) calendar days of delivery notification.
3. **Environment Security:** The Client is solely responsible for maintaining the confidentiality and security of their own cloud environments, API keys, database credentials, and production deployments.

### 2.3 Service Level & Warranties
- **Deterministic Gates:** Delivered software must pass the automated 13-point verification gate and domain acceptance tests prior to deployment.
- **Bug Fix Warranty:** Hermes guarantees a thirty (30) day warranty period following final milestone signoff covering defects violating the approved SOW specifications.
- **No Guarantee of Third-Party APIs:** Hermes is not liable for service outages, rate limiting, policy changes, or deprecations caused by external providers (e.g. OpenAI, Anthropic, Razorpay, WhatsApp Cloud API, Stripe, Telegram).

---

## 3. Standard Statement of Work (SOW) Template

```markdown
# STATEMENT OF WORK (SOW)

**Engagement ID:** SOW-2026-{{LEAD_ID}}
**Date:** {{DATE}}
**Client Name / Entity:** {{CLIENT_NAME}} ({{COMPANY_NAME}})
**Agency:** Hermes AI Automation Studio (Mohd Ashfaq)

### 1. Project Title & Objective
**Title:** {{PROJECT_TITLE}}
**Objective:** {{PROJECT_OBJECTIVE}}

### 2. Deliverable Scope
The project includes:
- Production-ready FastAPI application (`main.py`)
- Interactive web user interface (`index.html`, `styles.css`, `app.js`)
- Isolated configuration environment (`.env`)
- Local launch script (`run_local.bat`)
- Automated unit and acceptance test suites (`acceptance_tests.py`)
- Full Software Bill of Materials (SBOM) and License Manifest (`sbom.json`)

### 3. Exclusions (Out of Scope)
- Third-party merchant account verification or KYC approvals
- Hosting costs, domain name registrations, or cloud computing fees
- Maintenance beyond the 30-day post-delivery bug fix warranty

### 4. Milestone Schedule & Payment
- Milestone 1 (50% Advance): Mobilization, detailed scoping, and builder initiation.
- Milestone 2 (50% Final): Delivery, verification gate report, and source code transfer.
```

---

## 4. Advance Payment & Commercial Billing Terms

### 4.1 Staged Milestone Structure
To maintain financial safety and alignment, all custom software builds adhere to a two-phase milestone structure:
1. **Mobilization Advance (50%):** Invoiced upon approval of the Project Goal Plan (`STATUS: PLAN_APPROVED`). Builder execution begins only after payment confirmation from Razorpay/Stripe webhooks or verified bank transfer.
2. **Final Delivery Balance (50%):** Invoiced upon passing all 13 build verification gates and presenting the interactive demo (`STATUS: DEMO_READY`). Full unredacted source code and deployment keys are transferred upon final payment settlement (`STATUS: PAID`).

### 4.2 Minimum Engagement Thresholds
- Standard rapid turnaround deliverables: Base minimum ₹25,000 / $350 USD.
- Custom multi-system integrations: Base minimum ₹50,000 / $700 USD.

---

## 5. Refund & Cancellation Policy

1. **Discovery & Scoping Phase:** If the Client cancels before paying the Milestone 1 mobilization advance, no charges apply and the proposal is marked `CANCELLED`.
2. **Post-Mobilization (In-Build):** Once Milestone 1 is settled and autonomous scaffolding has commenced, the 50% mobilization fee is non-refundable, compensating for compute, token allocation, and scoping resources.
3. **Delivery Rejection:** If the delivered project fails to meet the explicit acceptance criteria codified in the approved SOW after two (2) remediation attempts, the Client is released from the Milestone 2 obligation.

---

## 6. Intellectual Property (IP) Ownership Terms

### 6.1 Client Deliverable Ownership
Upon receipt of 100% full payment (Milestones 1 & 2), the Client is granted exclusive, perpetual, worldwide ownership of:
- Custom business logic, proprietary data schemas, and domain algorithms created specifically for the project.
- Frontend designs, branding assets, and custom copy generated for the deliverable.

### 6.2 Agency Background IP Reservation
Hermes and Mohd Ashfaq retain sole and exclusive ownership of:
- The Hermes Autonomous Engine, graph architecture, state machine orchestrators, prompt chains, and builder scaffolding algorithms.
- Reusable libraries, utility modules, and open-source infrastructure components licensed under permissive terms (MIT/BSD/Apache-2.0).

---

## 7. Configurable Invoice & Tax Disclaimers

### 7.1 Goods and Services Tax (GST - India)
- **Domestic Transactions:** Subject to applicable GST (18% SAC Code 998314 - Information Technology Services). Invoices clearly delineate CGST (9%) + SGST (9%) or IGST (18%).
- **Export of Services:** B2B software services supplied to overseas clients outside India qualify as Zero-Rated Export of Services under Letter of Undertaking (LUT), exempt from GST collection subject to Foreign Inward Remittance Certificate (FIRC) compliance.

### 7.2 Reverse Charge & Currency
- Quotations and invoices display both INR (₹) and USD ($) equivalents where applicable.
- Payments via Razorpay or Stripe automatically convert at prevailing bank exchange rates.
- Invoices are digitally generated via ReportLab PDF engine and cryptographically signed in the `audit_log` table.

---

## 8. Dispute Resolution & Governing Law

This agreement shall be governed by and construed in accordance with the laws of India. Any dispute arising out of or in connection with engagements under this framework shall be subject to the exclusive jurisdiction of the competent courts in Hyderabad / Telangana, India.
