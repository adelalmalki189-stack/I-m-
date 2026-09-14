"""docs/API_SPEC_V0.1.md §7 — Strategy Parameters (read-only via API).

Read-only by design: editing happens via a migration/file change and a new
version row, never through the running API, to avoid an accidental edit
mid-session (§7's own stated reasoning).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.models.strategy_parameters import StrategyParameters

router = APIRouter(prefix="/strategy-parameters", tags=["strategy-parameters"])


class StrategyParametersResponse(BaseModel):
    version: str
    parameters: dict
    effective_from: datetime
    notes: str | None


def _to_response(row: StrategyParameters) -> StrategyParametersResponse:
    return StrategyParametersResponse(
        version=row.version, parameters=row.parameters, effective_from=row.effective_from, notes=row.notes
    )


@router.get("", response_model=StrategyParametersResponse)
async def get_active_strategy_parameters(
    session: AsyncSession = Depends(get_db_session),
) -> StrategyParametersResponse:
    """The current active version — most recent by `effective_from`."""
    result = await session.execute(select(StrategyParameters).order_by(StrategyParameters.effective_from.desc()))
    row = result.scalars().first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "NO_STRATEGY_PARAMETERS",
                "message": "No strategy_parameters version exists yet",
            },
        )
    return _to_response(row)


@router.get("/{version}", response_model=StrategyParametersResponse)
async def get_strategy_parameters_version(
    version: str, session: AsyncSession = Depends(get_db_session)
) -> StrategyParametersResponse:
    row = await session.get(StrategyParameters, version)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "VERSION_NOT_FOUND", "message": f"No strategy_parameters version '{version}'"},
        )
    return _to_response(row)
