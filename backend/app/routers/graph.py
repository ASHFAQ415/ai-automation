"""Graph inspection router for reviewers, debuggers, and monitoring dashboards."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.graph import workflow_graph

router = APIRouter(prefix="/api/graph", tags=["Graph Engineering"])


@router.get("/topology")
async def get_graph_topology():
    """Return the complete DAG topology specification (nodes, edges, human gates)."""
    return workflow_graph.get_topology()


@router.get("/state/{lead_id}")
async def get_lead_graph_state(lead_id: int, db: AsyncSession = Depends(get_db)):
    """Inspect the live execution state and audit trail for a specific lead workflow."""
    try:
        state = await workflow_graph.initialize_lead_state(lead_id, db)
        return state
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
