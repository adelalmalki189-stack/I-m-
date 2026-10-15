"""Staff authentication and role checks for the API.

A staff member logs in with email + password and receives an HttpOnly session cookie.
Every staff endpoint depends on `get_current_staff`; role-restricted endpoints add
`require_roles(...)`. Managers pass every role check.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.security import hash_token
from app.insurance.models import AuditEvent, InsuranceEmployeeRole, SalesEmployee, StaffSession

SESSION_COOKIE = "bud_session"

Role = InsuranceEmployeeRole
ROLE_LABELS = {
    Role.SALES_AGENT.value: "مبيعات",
    Role.UNDERWRITER.value: "اكتتاب",
    Role.CLAIMS_OFFICER.value: "مطالبات",
    Role.CUSTOMER_SERVICE.value: "خدمة عملاء",
    Role.MANAGER.value: "مدير",
}

# Common permission groups.
UNDERWRITING = (Role.UNDERWRITER, Role.MANAGER)
# Underwriters keep claim updates until the claims portal takes them over.
CLAIMS = (Role.CLAIMS_OFFICER, Role.UNDERWRITER, Role.MANAGER)
MANAGERS = (Role.MANAGER,)


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail={"error_code": "NOT_AUTHENTICATED", "message": "Login required"})


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def get_current_staff(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> SalesEmployee:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _unauthorized()
    result = await session.execute(
        select(StaffSession, SalesEmployee)
        .join(SalesEmployee, SalesEmployee.employee_id == StaffSession.employee_id)
        .where(StaffSession.token_hash == hash_token(token))
    )
    row = result.first()
    if row is None:
        raise _unauthorized()
    staff_session, employee = row
    expires_at = staff_session.expires_at
    if expires_at.tzinfo is None:  # SQLite returns naive datetimes
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC) or not employee.is_active:
        raise _unauthorized()
    return employee


def require_roles(*roles: Role) -> Callable[..., Coroutine[Any, Any, SalesEmployee]]:
    allowed = {r.value for r in roles} | {Role.MANAGER.value}

    async def checker(staff: SalesEmployee = Depends(get_current_staff)) -> SalesEmployee:
        if staff.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail={"error_code": "FORBIDDEN", "message": "Your role does not allow this action"},
            )
        return staff

    return checker


def audit(
    session: AsyncSession,
    actor: SalesEmployee | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    ip: str | None = None,
    **details: Any,
) -> None:
    """Queue an audit row on `session`; it is saved with the caller's commit."""
    session.add(
        AuditEvent(
            actor_id=actor.employee_id if actor else None,
            actor_name=actor.full_name if actor else "بوابة العميل",
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=json.dumps(details, ensure_ascii=False, default=str) if details else None,
            ip_address=ip,
        )
    )
