from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import result
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.result import Result
from app.models import User
from app.schemas.audit import AuditLogOut, AuditLogQuery
from app.schemas.page import Page, PageParams
from app.services import audit as audit_service

router = APIRouter(prefix="/audit", tags=["审计"])


@router.get(
    "/logs",
    summary="分页查询审计日志（admin）",
    response_model=Result[Page[AuditLogOut]],
)
async def list_logs(
    params: PageParams = Depends(),
    query: AuditLogQuery = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Result[Page[AuditLogOut]]:
    items, total = await audit_service.list_logs(
        db,
        params.page,
        params.page_size,
        action=query.action,
        actor_id=query.actor_id,
        created_at_from=query.created_at_from,
        created_at_to=query.created_at_to,
    )
    page = Page[AuditLogOut](
        total=total,
        page=params.page,
        page_size=params.page_size,
        items=[AuditLogOut.model_validate(i) for i in items],
    )
    return result.ok(data=page)
