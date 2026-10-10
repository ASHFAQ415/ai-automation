"""Routers package — includes all API routes and WhatsApp webhook."""
from app.routers.approvals import router as approvals_router
from app.routers.graph import router as graph_router
from app.routers.leads import router as leads_router
from app.routers.observability import router as observability_router
from app.routers.payments import router as payments_router
from app.routers.projects import router as projects_router
from app.routers.stats import router as stats_router
from app.routers.whatsapp import router as whatsapp_router

__all__ = [
    "approvals_router",
    "graph_router",
    "leads_router",
    "observability_router",
    "payments_router",
    "projects_router",
    "stats_router",
    "whatsapp_router",
]
