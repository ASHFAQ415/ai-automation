# Software License Inventory & Open-Source Compliance Manifest

**Author:** Mohd Ashfaq  
**Platform:** Hermes Autonomous AI Lead-to-Sales & Project Builder Engine  
**Version:** 2.0.0 (Production Release)  
**Governing Standard:** Master IDE Implementation Instruction — Section 14A.6  

---

## 1. Executive Summary

This document establishes the comprehensive Software Bill of Materials (SBOM) and open-source license inventory for both the **Hermes Platform Backend** and **Autonomous Client Deliverables**. Every dependency is audited to ensure permissive usage (MIT, Apache-2.0, BSD-3-Clause) and eliminate copyleft contamination (GPL/AGPL).

---

## 2. Platform Core Dependencies

| Package | Minimum Version | License | Category | Commercial Permissibility |
|---|---|---|---|---|
| **FastAPI** | `>=0.115.0` | MIT | Permissive | ✅ Full commercial rights |
| **Uvicorn** | `>=0.32.0` | BSD-3-Clause | Permissive | ✅ Full commercial rights |
| **Pydantic** | `>=2.10.0` | MIT | Permissive | ✅ Full commercial rights |
| **SQLAlchemy** | `>=2.0.36` | MIT | Permissive | ✅ Full commercial rights |
| **AioSQLite** | `>=0.20.0` | MIT | Permissive | ✅ Full commercial rights |
| **Python-Dotenv**| `>=1.0.1` | BSD-3-Clause | Permissive | ✅ Full commercial rights |
| **HTTPX** | `>=0.28.0` | BSD-3-Clause | Permissive | ✅ Full commercial rights |
| **Starlette** | `>=0.40.0` | BSD-3-Clause | Permissive | ✅ Full commercial rights |
| **ReportLab** | `>=4.0.0` | BSD-3-Clause | Permissive | ✅ Commercial PDF generation |
| **Pytest** | `>=8.0.0` | MIT | Permissive | ✅ Test tooling |
| **Pytest-Asyncio**| `>=0.24.0` | Apache-2.0 | Permissive | ✅ Test tooling |
| **Ruff** | `>=0.8.0` | MIT | Permissive | ✅ Static analysis |
| **Bandit** | `>=1.7.0` | Apache-2.0 | Permissive | ✅ Security scanning |

---

## 3. Client Deliverables Dependency Standard

Generated client projects in `projects/` are standardized on zero-copyleft, minimal dependency footprints:

```text
fastapi>=0.115.0      # MIT License
uvicorn>=0.32.0      # BSD-3-Clause License
pydantic>=2.10.0     # MIT License
python-dotenv>=1.0.1 # BSD-3-Clause License
httpx>=0.28.0        # BSD-3-Clause License
```

### 3.1 License Classification Matrix
- **Permissive (100% of Stack):** MIT and BSD-3-Clause permits unencumbered proprietary commercial redistribution, closed-source client deployment, and private modification without disclosing source code.
- **Copyleft (0% of Stack):** No GPL, AGPL, or SSPL licensed libraries are bundled in client runtimes or core execution engines.

---

## 4. Automated SBOM Generation Engine

The platform provides programmatic Software Bill of Materials (SBOM) generation via:
```python
from app.services.sbom_inspector import generate_cyclonedx_sbom, inspect_project_licenses

# Generates CycloneDX 1.5 JSON SBOM
sbom = generate_cyclonedx_sbom(project_dir=Path("projects/client_project_1"))
```

Deliverables export `sbom.json` during the 13-point build verification gate to provide institutional clients with immediate compliance documentation.
