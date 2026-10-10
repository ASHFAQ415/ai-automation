"""Outreach Control, Rate Limiting, and Opt-Out Compliance.

Implements Phase 5.3 of Master Specification:
- Strict compliance with contact_status (OPTED_IN, OPTED_OUT, DO_NOT_CONTACT)
- Configurable sliding-window rate limits (default 10 contacts/hr, 50 contacts/day)
- Safe rejection of non-compliant outreach attempts
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
import threading

from app.models import Lead

logger = logging.getLogger("outreach_controller")

DEFAULT_MAX_PER_HOUR = 10
DEFAULT_MAX_PER_DAY = 50


class OutreachRateLimiter:
    """Thread-safe sliding window rate limiter for outbound lead communications."""

    def __init__(self, max_per_hour: int = DEFAULT_MAX_PER_HOUR, max_per_day: int = DEFAULT_MAX_PER_DAY):
        self.max_per_hour = max_per_hour
        self.max_per_day = max_per_day
        self.history: list[datetime] = []
        self._lock = threading.Lock()

    def record_attempt(self) -> None:
        """Record an executed contact timestamp."""
        with self._lock:
            self.history.append(datetime.now(timezone.utc))

    def check_rate_limits(self) -> tuple[bool, str]:
        """Check if sending another message would exceed hourly or daily limits.

        Returns:
            (allowed: bool, reason: str)
        """
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)
        one_day_ago = now - timedelta(days=1)

        with self._lock:
            # Prune records older than 24 hours
            self.history = [ts for ts in self.history if ts > one_day_ago]

            hourly_count = sum(1 for ts in self.history if ts > one_hour_ago)
            daily_count = len(self.history)

            if hourly_count >= self.max_per_hour:
                return False, f"Hourly outreach rate limit reached ({hourly_count}/{self.max_per_hour} per hour)"

            if daily_count >= self.max_per_day:
                return False, f"Daily outreach rate limit reached ({daily_count}/{self.max_per_day} per day)"

            return True, f"Within limits ({hourly_count}/hr, {daily_count}/day)"


# Global singleton rate limiter
outreach_limiter = OutreachRateLimiter()


def can_contact_lead(lead: Lead) -> tuple[bool, str]:
    """Check lead opt-out status and regulatory contact compliance.

    Returns:
        (allowed: bool, reason: str)
    """
    status = (lead.contact_status or "OPTED_IN").upper()

    if status == "OPTED_OUT":
        return False, f"Lead #{lead.id} ({lead.name}) has opted out of automated outreach."

    if status == "DO_NOT_CONTACT":
        return False, f"Lead #{lead.id} ({lead.name}) is marked DO_NOT_CONTACT."

    if status != "OPTED_IN":
        return False, f"Lead #{lead.id} has unrecognized contact status '{status}'."

    # Check global velocity limits
    allowed, rate_reason = outreach_limiter.check_rate_limits()
    if not allowed:
        return False, rate_reason

    return True, f"Lead #{lead.id} is cleared for outreach."
