"""Projects router to list, inspect, and explore built client projects."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_api_key
from app.models import Lead, ProjectBuild
from app.schemas import BuildResponse

router = APIRouter(prefix="/api/projects", tags=["Projects"])


@router.get("", response_model=list[BuildResponse])
async def list_projects(db: AsyncSession = Depends(get_db)):
    """List all autonomous projects."""
    query = await db.execute(select(ProjectBuild).order_by(desc(ProjectBuild.created_at)))
    return query.scalars().all()


@router.get("/{build_id}")
async def get_project(build_id: int, db: AsyncSession = Depends(get_db)):
    """Get project build details with file list."""
    query = await db.execute(select(ProjectBuild).where(ProjectBuild.id == build_id))
    build = query.scalar_one_or_none()
    if not build:
        raise HTTPException(status_code=404, detail="Project build not found")
    
    files = json.loads(build.generated_files) if build.generated_files else []
    return {
        "id": build.id,
        "lead_id": build.lead_id,
        "project_name": build.project_name,
        "project_path": build.project_path,
        "status": build.status,
        "progress_percent": build.progress_percent,
        "files": files,
        "build_logs": build.build_logs,
        "created_at": build.created_at,
        "completed_at": build.completed_at
    }


@router.get("/{build_id}/file")
async def get_project_file_content(
    build_id: int,
    file_name: str = Query(..., description="Relative file path"),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve raw file content for previewing in the dashboard."""
    query = await db.execute(select(ProjectBuild).where(ProjectBuild.id == build_id))
    build = query.scalar_one_or_none()
    if not build:
        raise HTTPException(status_code=404, detail="Project build not found")

    base_path = Path(build.project_path).resolve()
    target_path = (base_path / file_name).resolve()

    # Security check: ensure within project dir
    if not str(target_path).startswith(str(base_path)):
        raise HTTPException(status_code=403, detail="Access denied: path traversal")

    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"file_name": file_name, "content": content, "size": len(content)}
    except UnicodeDecodeError:
        return {"file_name": file_name, "content": "[Binary file]", "size": target_path.stat().st_size}


@router.get("/{build_id}/download-zip")
async def download_project_zip(build_id: int, db: AsyncSession = Depends(get_db)):
    """Package project directory as a downloadable ZIP."""
    import io
    import zipfile

    from fastapi.responses import StreamingResponse

    query = await db.execute(select(ProjectBuild).where(ProjectBuild.id == build_id))
    build = query.scalar_one_or_none()
    if not build:
        raise HTTPException(status_code=404, detail="Project build not found")

    project_path = Path(build.project_path)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail="Project directory not found on disk")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(project_path):
            for file in files:
                file_full = Path(root) / file
                arcname = file_full.relative_to(project_path)
                zip_file.write(file_full, arcname)

    zip_buffer.seek(0)
    filename = f"{project_path.name}.zip"
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/{build_id}/cancel", dependencies=[Depends(require_api_key)])
async def cancel_project_build(build_id: int, db: AsyncSession = Depends(get_db)):
    """Cancel an active project build. Requires API key."""
    from datetime import datetime

    from app.services.notifier import broadcast_ws

    query = await db.execute(select(ProjectBuild).where(ProjectBuild.id == build_id))
    build = query.scalar_one_or_none()
    if not build:
        raise HTTPException(status_code=404, detail="Project build not found")

    build.status = "CANCELLED"
    build.build_logs = (build.build_logs or "") + f"[{datetime.utcnow().isoformat()}] ✕ Build cancelled by user.\n"
    
    # Revert lead status so it can be re-run or edited
    lead_query = await db.execute(select(Lead).where(Lead.id == build.lead_id))
    lead = lead_query.scalar_one_or_none()
    if lead:
        lead.status = "PLAN_APPROVED"

    await db.commit()
    await broadcast_ws("BUILD_CANCELLED", {"build_id": build.id})
    return {"status": "success", "message": f"Project build #{build_id} cancelled."}


@router.delete("/{build_id}", dependencies=[Depends(require_api_key)])
async def delete_project(build_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a project record and remove its files from disk. Requires API key."""
    import shutil

    from app.services.notifier import broadcast_ws

    query = await db.execute(select(ProjectBuild).where(ProjectBuild.id == build_id))
    build = query.scalar_one_or_none()
    if not build:
        raise HTTPException(status_code=404, detail="Project build not found")

    # Delete folder from disk
    project_path = Path(build.project_path)
    if project_path.exists():
        shutil.rmtree(project_path, ignore_errors=True)

    # Revert lead status
    lead_query = await db.execute(select(Lead).where(Lead.id == build.lead_id))
    lead = lead_query.scalar_one_or_none()
    if lead:
        lead.status = "PLAN_APPROVED"

    await db.delete(build)
    await db.commit()
    await broadcast_ws("PROJECT_DELETED", {"build_id": build_id})
    return {"status": "success", "message": f"Project #{build_id} deleted."}

