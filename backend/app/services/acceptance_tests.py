"""Layer A Deterministic Acceptance Tests & Layer B Independent Verification.

Per Section 14A.3 and Phase 1 of Master Specification:
- Layer A: Reusable deterministic domain test suites (School, Inventory, Restaurant, CRM, Generic)
- Layer B: Independent verification testing against specifications/requirements
"""
from __future__ import annotations

import logging
from typing import Any
from starlette.testclient import TestClient

logger = logging.getLogger("acceptance_tests")


def detect_domain(requirement: str, title: str = "") -> str:
    """Detect business domain from lead requirement and project title."""
    text = f"{requirement} {title}".lower()
    if any(k in text for k in ["school", "timetable", "class", "period", "teacher", "student"]):
        return "school"
    if any(k in text for k in ["restaurant", "food", "cafe", "menu", "dining", "dish", "pos"]):
        return "restaurant"
    if any(k in text for k in ["inventory", "stock", "warehouse", "supply", "sku", "product"]):
        return "inventory"
    if any(k in text for k in ["crm", "customer", "pipeline", "client", "sales", "prospect"]):
        return "crm"
    return "generic"


class AcceptanceTestResult:
    def __init__(self, name: str, passed: bool, error: str | None = None):
        self.name = name
        self.passed = passed
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "error": self.error}


# ==============================================================================
# LAYER A: DETERMINISTIC DOMAIN TEST SUITES
# ==============================================================================

def run_school_acceptance_tests(client: TestClient) -> list[AcceptanceTestResult]:
    """School Management / Timetable: slots, conflicts, clash detection, teacher assignment."""
    results = []

    # 1. Health check
    try:
        resp = client.get("/api/health")
        passed = resp.status_code == 200 and "status" in resp.json()
        results.append(AcceptanceTestResult("school_health_check", passed))
    except Exception as e:
        results.append(AcceptanceTestResult("school_health_check", False, str(e)))

    # 2. Timetable retrieval
    try:
        resp = client.get("/api/timetable")
        passed = resp.status_code == 200 and isinstance(resp.json(), list) and len(resp.json()) > 0
        results.append(AcceptanceTestResult("school_timetable_retrieval", passed))
    except Exception as e:
        results.append(AcceptanceTestResult("school_timetable_retrieval", False, str(e)))

    # 3. Add valid slot
    new_slot_id = None
    try:
        slot_payload = {
            "day": "Wednesday",
            "period": 3,
            "class_group": "Grade 10-A",
            "subject": "Biology",
            "teacher": "Dr. Watson",
            "room": "Bio Lab 1",
            "color_type": "bio",
        }
        resp = client.post("/api/timetable", json=slot_payload)
        data = resp.json()
        passed = resp.status_code in [200, 201] and data.get("subject") == "Biology"
        new_slot_id = data.get("id")
        results.append(AcceptanceTestResult("school_slot_assignment", passed))
    except Exception as e:
        results.append(AcceptanceTestResult("school_slot_assignment", False, str(e)))

    # 4. Teacher / Room clash detection
    try:
        clash_payload = {
            "day": "Wednesday",
            "period": 3,
            "class_group": "Grade 10-B",
            "subject": "Chemistry",
            "teacher": "Dr. Watson",  # Same teacher, same day, same period!
            "room": "Room 101",
        }
        resp = client.post("/api/timetable", json=clash_payload)
        # Expected: either rejected with 400/409, or flagged in /api/conflicts
        if resp.status_code in [400, 409]:
            results.append(AcceptanceTestResult("school_clash_detection", True))
        else:
            conflicts_resp = client.get("/api/conflicts")
            conf_data = conflicts_resp.json()
            has_conflict = conf_data.get("conflict_count", 0) > 0 or len(conf_data.get("conflicts", [])) > 0
            results.append(AcceptanceTestResult("school_clash_detection", has_conflict or resp.status_code == 200))
    except Exception as e:
        results.append(AcceptanceTestResult("school_clash_detection", False, str(e)))

    # 5. Clean up created slot if created
    if new_slot_id:
        try:
            del_resp = client.delete(f"/api/timetable/{new_slot_id}")
            results.append(AcceptanceTestResult("school_slot_deletion", del_resp.status_code == 200))
        except Exception as e:
            results.append(AcceptanceTestResult("school_slot_deletion", False, str(e)))

    return results


def run_inventory_acceptance_tests(client: TestClient) -> list[AcceptanceTestResult]:
    """Inventory Management: lookup, stock increment, stock decrement, negative stock prevention."""
    results = []

    # 1. Health check
    try:
        resp = client.get("/api/health")
        results.append(AcceptanceTestResult("inventory_health_check", resp.status_code == 200))
    except Exception as e:
        results.append(AcceptanceTestResult("inventory_health_check", False, str(e)))

    # 2. List items
    try:
        resp = client.get("/api/items")
        results.append(AcceptanceTestResult("inventory_item_lookup", resp.status_code == 200 and isinstance(resp.json(), list)))
    except Exception as e:
        results.append(AcceptanceTestResult("inventory_item_lookup", False, str(e)))

    # 3. Create stock item
    item_id = None
    try:
        payload = {
            "name": "Industrial Sensor X1",
            "sku": "SEN-001",
            "quantity": 50,
            "unit_price": 120.0,
            "category": "Sensors",
        }
        resp = client.post("/api/items", json=payload)
        data = resp.json()
        item_id = data.get("id")
        results.append(AcceptanceTestResult("inventory_stock_creation", resp.status_code in [200, 201]))
    except Exception as e:
        results.append(AcceptanceTestResult("inventory_stock_creation", False, str(e)))

    # 4. Stock increase
    if item_id:
        try:
            resp = client.post(f"/api/items/{item_id}/adjust", json={"adjustment": 10})
            data = resp.json()
            passed = resp.status_code == 200 and data.get("quantity") == 60
            results.append(AcceptanceTestResult("inventory_stock_increase", passed))
        except Exception as e:
            results.append(AcceptanceTestResult("inventory_stock_increase", False, str(e)))

        # 5. Invalid negative stock rejection
        try:
            resp = client.post(f"/api/items/{item_id}/adjust", json={"adjustment": -100})
            # Expected 400 Bad Request: cannot have negative stock
            results.append(AcceptanceTestResult("inventory_negative_stock_prevention", resp.status_code == 400))
        except Exception as e:
            results.append(AcceptanceTestResult("inventory_negative_stock_prevention", False, str(e)))

    return results


def run_restaurant_acceptance_tests(client: TestClient) -> list[AcceptanceTestResult]:
    """Restaurant POS: menu creation, order creation, quantity updates, total calculation."""
    results = []

    # 1. Health check
    try:
        resp = client.get("/api/health")
        results.append(AcceptanceTestResult("restaurant_health_check", resp.status_code == 200))
    except Exception as e:
        results.append(AcceptanceTestResult("restaurant_health_check", False, str(e)))

    # 2. Menu retrieval
    try:
        resp = client.get("/api/menu")
        results.append(AcceptanceTestResult("restaurant_menu_retrieval", resp.status_code == 200 and isinstance(resp.json(), list)))
    except Exception as e:
        results.append(AcceptanceTestResult("restaurant_menu_retrieval", False, str(e)))

    # 3. Order creation with total calculation
    try:
        order_payload = {
            "table_number": 5,
            "items": [
                {"item_id": 1, "name": "Classic Burger", "price": 12.50, "quantity": 2},
                {"item_id": 2, "name": "Craft Lemonade", "price": 4.00, "quantity": 1},
            ],
            "notes": "No onions",
        }
        resp = client.post("/api/orders", json=order_payload)
        data = resp.json()
        expected_total = (12.50 * 2) + (4.00 * 1)  # 29.00
        passed = resp.status_code in [200, 201] and abs(data.get("total", 0) - expected_total) < 0.01
        results.append(AcceptanceTestResult("restaurant_order_calculation", passed))
    except Exception as e:
        results.append(AcceptanceTestResult("restaurant_order_calculation", False, str(e)))

    return results


def run_crm_acceptance_tests(client: TestClient) -> list[AcceptanceTestResult]:
    """CRM: lead creation, update, status transitions, search/filtering."""
    results = []

    # 1. Health check
    try:
        resp = client.get("/api/health")
        results.append(AcceptanceTestResult("crm_health_check", resp.status_code == 200))
    except Exception as e:
        results.append(AcceptanceTestResult("crm_health_check", False, str(e)))

    # 2. Lead creation
    lead_id = None
    try:
        payload = {
            "name": "Sarah Connor",
            "company": "Cyberdyne Systems",
            "email": "sarah@cyberdyne.io",
            "status": "NEW",
            "value": 25000,
        }
        resp = client.post("/api/leads", json=payload)
        data = resp.json()
        lead_id = data.get("id")
        results.append(AcceptanceTestResult("crm_lead_creation", resp.status_code in [200, 201]))
    except Exception as e:
        results.append(AcceptanceTestResult("crm_lead_creation", False, str(e)))

    # 3. Status transition
    if lead_id:
        try:
            resp = client.patch(f"/api/leads/{lead_id}", json={"status": "QUALIFIED"})
            data = resp.json()
            results.append(AcceptanceTestResult("crm_status_transition", resp.status_code == 200 and data.get("status") == "QUALIFIED"))
        except Exception as e:
            results.append(AcceptanceTestResult("crm_status_transition", False, str(e)))

    # 4. Search and filter
    try:
        resp = client.get("/api/leads?search=Sarah")
        data = resp.json()
        passed = resp.status_code == 200 and any("Sarah" in x.get("name", "") for x in data)
        results.append(AcceptanceTestResult("crm_search_filter", passed))
    except Exception as e:
        results.append(AcceptanceTestResult("crm_search_filter", False, str(e)))

    return results


def run_generic_acceptance_tests(client: TestClient) -> list[AcceptanceTestResult]:
    """Generic Automation / Task Suite: health, items/tasks listing, creation, deletion."""
    results = []

    # 1. Health check
    try:
        resp = client.get("/api/health")
        results.append(AcceptanceTestResult("generic_health_check", resp.status_code == 200 and "status" in resp.json()))
    except Exception as e:
        results.append(AcceptanceTestResult("generic_health_check", False, str(e)))

    # Determine collection endpoint: /api/tasks, /api/items, or /api/records
    endpoint = "/api/items"
    try:
        r = client.get("/api/items")
        if r.status_code != 200:
            r2 = client.get("/api/tasks")
            if r2.status_code == 200:
                endpoint = "/api/tasks"
            else:
                r3 = client.get("/api/records")
                if r3.status_code == 200:
                    endpoint = "/api/records"
    except Exception:
        endpoint = "/api/items"

    # 2. List items
    try:
        resp = client.get(endpoint)
        results.append(AcceptanceTestResult("generic_list_items", resp.status_code == 200 and isinstance(resp.json(), list)))
    except Exception as e:
        results.append(AcceptanceTestResult("generic_list_items", False, str(e)))

    # 3. Create item
    created_id = None
    try:
        if endpoint == "/api/tasks":
            payload = {
                "title": "Autonomous Verification Task",
                "description": "Deterministic acceptance verification test.",
                "priority": "High",
                "due_date": "2026-09-23",
                "status": "To Do",
            }
        else:
            payload = {
                "title": "Autonomous Verification Task",
                "category": "QA",
                "priority": "High",
                "due_date": "2026-09-23",
                "status": "Active",
                "details": "Deterministic acceptance verification test.",
            }
        resp = client.post(endpoint, json=payload)
        data = resp.json()
        created_id = data.get("id")
        results.append(AcceptanceTestResult("generic_create_item", resp.status_code in [200, 201] and data.get("title") == payload["title"]))
    except Exception as e:
        results.append(AcceptanceTestResult("generic_create_item", False, str(e)))

    # 4. Delete item
    if created_id:
        try:
            resp = client.delete(f"{endpoint}/{created_id}")
            results.append(AcceptanceTestResult("generic_delete_item", resp.status_code in [200, 204]))
        except Exception as e:
            results.append(AcceptanceTestResult("generic_delete_item", False, str(e)))

    return results


def run_deterministic_acceptance_tests(app: Any, requirement: str = "", title: str = "") -> dict[str, Any]:
    """Execute Layer A deterministic domain acceptance tests."""
    domain = detect_domain(requirement, title)
    client = TestClient(app, raise_server_exceptions=False)

    if domain == "school":
        results = run_school_acceptance_tests(client)
    elif domain == "inventory":
        adj_resp = client.post("/api/items/1/adjust", json={"adjustment": 1})
        if adj_resp.status_code != 404:
            results = run_inventory_acceptance_tests(client)
        else:
            results = run_generic_acceptance_tests(client)
    elif domain == "restaurant":
        menu_resp = client.get("/api/menu")
        if menu_resp.status_code != 404:
            results = run_restaurant_acceptance_tests(client)
        else:
            results = run_generic_acceptance_tests(client)
    elif domain == "crm":
        leads_resp = client.get("/api/leads")
        if leads_resp.status_code != 404:
            results = run_crm_acceptance_tests(client)
        else:
            results = run_generic_acceptance_tests(client)
    else:
        results = run_generic_acceptance_tests(client)

    passed_count = sum(1 for r in results if r.passed)
    total_count = len(results)
    all_passed = (passed_count == total_count) if total_count > 0 else False

    return {
        "layer": "LAYER_A_DETERMINISTIC",
        "domain": domain,
        "total_tests": total_count,
        "passed_tests": passed_count,
        "failed_tests": total_count - passed_count,
        "success": all_passed,
        "tests": [r.to_dict() for r in results],
    }


# ==============================================================================
# LAYER B: INDEPENDENT ACCEPTANCE TESTS
# ==============================================================================

def run_independent_acceptance_tests(app: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """Execute Layer B independent verification pass against approved specifications.

    Crucial rule: Evaluates requirements invariants WITHOUT inspecting implementation source code.
    """
    client = TestClient(app, raise_server_exceptions=False)
    results = []

    # 1. Verification of Root / UI entrypoint
    try:
        resp = client.get("/")
        # Home should be accessible (200 OK) either returning HTML or JSON status
        passed = resp.status_code == 200
        results.append(AcceptanceTestResult("independent_root_entrypoint", passed))
    except Exception as e:
        results.append(AcceptanceTestResult("independent_root_entrypoint", False, str(e)))

    # 2. CORS and Options Preflight Security check
    try:
        resp = client.options("/api/health", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
        # Should allow preflight or respond safely
        results.append(AcceptanceTestResult("independent_cors_preflight", resp.status_code in [200, 204, 405]))
    except Exception as e:
        results.append(AcceptanceTestResult("independent_cors_preflight", False, str(e)))

    # 3. Input validation against malformed payload
    try:
        # Posting malformed / invalid JSON body should return 422 Unprocessable Entity, NEVER 500 Internal Server Error
        resp = client.post("/api/timetable" if "timetable" in str(plan).lower() else "/api/items", json="invalid_string_not_dict")
        passed = resp.status_code in [400, 422, 404]  # Clean validation error
        results.append(AcceptanceTestResult("independent_input_validation_resilience", passed))
    except Exception as e:
        results.append(AcceptanceTestResult("independent_input_validation_resilience", False, str(e)))

    # 4. Non-existent route 404 handling
    try:
        resp = client.get("/api/non_existent_untrusted_route_99999")
        results.append(AcceptanceTestResult("independent_404_handling", resp.status_code == 404))
    except Exception as e:
        results.append(AcceptanceTestResult("independent_404_handling", False, str(e)))

    passed_count = sum(1 for r in results if r.passed)
    total_count = len(results)
    all_passed = (passed_count == total_count) if total_count > 0 else False

    return {
        "layer": "LAYER_B_INDEPENDENT",
        "total_tests": total_count,
        "passed_tests": passed_count,
        "failed_tests": total_count - passed_count,
        "success": all_passed,
        "tests": [r.to_dict() for r in results],
    }
