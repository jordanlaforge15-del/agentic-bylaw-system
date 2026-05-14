"""Admin endpoints for the case-credit billing model.

Mirrors the dormant-by-default pattern of ``advisor.billing``:
endpoints stay un-mounted unless the operator sets
``ADVISOR_ADMIN_API_ENABLED=true`` AND populates
``ADVISOR_ADMIN_CLERK_USER_IDS`` with a comma-separated allowlist.
"""
from advisor.admin.router import build_admin_router, build_dormant_admin_router

__all__ = ["build_admin_router", "build_dormant_admin_router"]
