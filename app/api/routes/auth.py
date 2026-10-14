"""Staff login / logout / profile."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ROLE_LABELS, SESSION_COOKIE, audit, client_ip, get_current_staff
from app.api.deps import get_db_session
from app.core.config import settings
from app.core.security import RateLimiter, hash_password, hash_token, new_token, password_problem, verify_password
from app.insurance.models import SalesEmployee, StaffSession

router = APIRouter(prefix="/auth", tags=["auth"])

login_limiter = RateLimiter(settings.login_max_attempts, settings.login_lock_minutes * 60)
# Verifying against a real hash when the email is unknown keeps response times equal,
# so the endpoint doesn't reveal which emails belong to staff.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class StaffProfile(BaseModel):
    employee_id: str
    full_name: str
    email: str
    role: str
    role_label: str


def profile(employee: SalesEmployee) -> StaffProfile:
    return StaffProfile(
        employee_id=employee.employee_id,
        full_name=employee.full_name,
        email=employee.email,
        role=employee.role,
        role_label=ROLE_LABELS.get(employee.role, employee.role),
    )


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


@router.post("/login", response_model=StaffProfile)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> StaffProfile:
    key = str(body.email).lower()
    if login_limiter.blocked(key):
        raise _error(429, "TOO_MANY_ATTEMPTS", "Too many failed attempts; try again later")

    result = await session.execute(select(SalesEmployee).where(func.lower(SalesEmployee.email) == key))
    employee = result.scalar_one_or_none()
    valid = verify_password(body.password, employee.password_hash if employee else _DUMMY_HASH)
    if employee is None or not valid or not employee.is_active:
        login_limiter.hit(key)
        audit(
            session,
            employee,
            "login_failed",
            "employee",
            employee.employee_id if employee else None,
            ip=client_ip(request),
            email=key,
        )
        await session.commit()
        raise _error(401, "INVALID_CREDENTIALS", "Wrong email or password")

    login_limiter.reset(key)
    token, token_hash = new_token()
    session.add(
        StaffSession(
            employee_id=employee.employee_id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(hours=settings.session_hours),
            ip_address=client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:255],
        )
    )
    employee.last_login_at = datetime.now(UTC)
    audit(session, employee, "login", "employee", employee.employee_id, ip=client_ip(request))
    await session.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return profile(employee)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_db_session)) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await session.execute(delete(StaffSession).where(StaffSession.token_hash == hash_token(token)))
        await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=StaffProfile)
async def me(staff: SalesEmployee = Depends(get_current_staff)) -> StaffProfile:
    return profile(staff)


@router.post("/change-password", status_code=204)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    staff: SalesEmployee = Depends(get_current_staff),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    if not verify_password(body.current_password, staff.password_hash):
        raise _error(400, "INVALID_CREDENTIALS", "Current password is wrong")
    problem = password_problem(body.new_password)
    if problem:
        raise _error(422, "WEAK_PASSWORD", problem)
    staff.password_hash = hash_password(body.new_password)
    # Sign out every other session of this employee.
    current = hash_token(request.cookies.get(SESSION_COOKIE, ""))
    await session.execute(
        delete(StaffSession).where(
            StaffSession.employee_id == staff.employee_id, StaffSession.token_hash != current
        )
    )
    audit(session, staff, "password_changed", "employee", staff.employee_id, ip=client_ip(request))
    await session.commit()
