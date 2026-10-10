"""AdminUser Authorization & RBAC for Control Plane and Telegram Bot.

Implements Phase 5.2 of Master Specification:
- AdminUser model authorization
- Multi-tier roles: SUPERADMIN > OPERATOR > VIEWER
- Bootstrap admin configuration fallback
"""
from __future__ import annotations

import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AdminUser

logger = logging.getLogger("admin_auth")

ROLE_HIERARCHY = {
    "SUPERADMIN": 3,
    "OPERATOR": 2,
    "VIEWER": 1,
}


async def is_authorized_admin(
    db: AsyncSession,
    telegram_user_id: int | str,
    required_role: str = "OPERATOR",
) -> tuple[bool, str]:
    """Check if telegram user has authorization for the given role level.

    Returns:
        (authorized: bool, reason: str)
    """
    str_user_id = str(telegram_user_id)
    required_weight = ROLE_HIERARCHY.get(required_role.upper(), 2)

    # 1. Check Bootstrap Superadmin from settings
    configured_bootstrap_id = str(getattr(settings, "TELEGRAM_CHAT_ID", "")).strip()
    if configured_bootstrap_id and str_user_id == configured_bootstrap_id:
        return True, "Authorized via Bootstrap Superadmin configuration"

    # 2. Check AdminUser database record
    query = select(AdminUser).where(AdminUser.telegram_user_id == str_user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        logger.warning("Unauthorized access attempt by Telegram user %s", str_user_id)
        return False, f"User {str_user_id} is not registered in admin directory"

    if not user.enabled:
        return False, f"Admin account for {str_user_id} is disabled"

    user_weight = ROLE_HIERARCHY.get(user.role.upper(), 1)
    if user_weight >= required_weight:
        return True, f"Authorized with role {user.role}"

    return False, f"Insufficient role: user has {user.role}, required {required_role}"
