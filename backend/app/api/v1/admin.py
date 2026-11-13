from __future__ import annotations

import time
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.api.deps import get_session_factory
from app.cache.service import invalidate_price_cache, invalidate_poi_cache
from app.core.config import settings
from app.core.errors import BizError, ErrorCode
from app.core.security import decode_admin_jwt, issue_admin_jwt, verify_password
from app.models import Poi
from app.models.poi import (
    XUZHOU_LAT_MAX,
    XUZHOU_LAT_MIN,
    XUZHOU_LON_MAX,
    XUZHOU_LON_MIN,
)
from app.repositories.itinerary_repo import ChangeEventRepository, ItineraryRepository
from app.repositories.knowledge_repo import (
    AuditLogRepository,
    KnowledgeChunkRepository,
    KnowledgeRepository,
)
from app.repositories.poi_repo import PoiRepository, PriceRepository
from app.repositories.user_repo import UserRepository
from app.services.audit_service import AuditService
from app.services.knowledge_service import KnowledgeService
from app.services.price_service import PriceService, to_price_dto
from app.services.suggestion_service import SuggestionService

router = APIRouter(prefix="/admin", tags=["admin"])

POI_CATEGORIES = ("景点", "博物馆", "餐饮", "商圈", "交通枢纽")
PRICE_TYPES = ("门票", "交通", "餐饮")

MAX_LOGIN_FAILURES = 5
LOGIN_LOCK_SECONDS = 10 * 60
_login_failures: dict[str, dict] = {}


def _reset_login_guard() -> None:
    _login_failures.clear()


def _lock_remaining_seconds(username: str) -> int:
    state = _login_failures.get(username)
    if not state:
        return 0
    locked_until = state.get("locked_until")
    if locked_until is None:
        return 0
    remaining = int(locked_until - time.monotonic())
    if remaining <= 0:
        _login_failures.pop(username, None)
        return 0
    return remaining


def _record_login_failure(username: str) -> None:
    state = _login_failures.setdefault(username, {"failures": 0, "locked_until": None})
    state["failures"] += 1
    if state["failures"] >= MAX_LOGIN_FAILURES:
        state["locked_until"] = time.monotonic() + LOGIN_LOCK_SECONDS


def _clear_login_failures(username: str) -> None:
    _login_failures.pop(username, None)


def _to_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _validate_poi_invariants(
    *,
    name: str,
    category: str,
    tags: list[str],
    longitude: float,
    latitude: float,
    visit_minutes: int,
) -> None:
    if not name or not name.strip():
        raise BizError(ErrorCode.PARAM_INVALID, "POI 名称必填")
    if category not in POI_CATEGORIES:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            f"类别必须是 {'/'.join(POI_CATEGORIES)} 之一",
        )
    if not tags:
        raise BizError(ErrorCode.PARAM_INVALID, "主题标签至少 1 个")
    if not (XUZHOU_LON_MIN <= longitude <= XUZHOU_LON_MAX):
        raise BizError(ErrorCode.PARAM_INVALID, "经度超出徐州市区范围")
    if not (XUZHOU_LAT_MIN <= latitude <= XUZHOU_LAT_MAX):
        raise BizError(ErrorCode.PARAM_INVALID, "纬度超出徐州市区范围")
    if visit_minutes <= 0:
        raise BizError(ErrorCode.PARAM_INVALID, "常规游览时长必须大于 0 分钟")


def _poi_dto(poi: Poi) -> dict:
    return {
        "poi_id": str(poi.id),
        "name": poi.name,
        "category": poi.category,
        "tags": list(poi.tags),
        "location": {"lng": float(poi.longitude), "lat": float(poi.latitude)},
        "city": poi.city,
        "visit_minutes": poi.visit_minutes,
        "indoor": poi.indoor,
        "status": poi.status,
        "description": poi.description,
        "source_channel": poi.source_channel,
        "collected_at": poi.collected_at.isoformat(),
    }


def _audit_log_dto(log) -> dict:
    return {
        "audit_id": str(log.id),
        "operator_id": str(log.operator_id),
        "action": log.action,
        "entity_type": log.entity_type,
        "entity_id": str(log.entity_id),
        "before_value": log.before_value,
        "after_value": log.after_value,
        "operated_at": log.operated_at.isoformat() if log.operated_at else None,
    }


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class PriceSubmitRequest(BaseModel):
    poi_id: int
    price_type: str
    amount: float
    source_channel: str
    collected_at: date
    valid_until: date


class PriceReviewRequest(BaseModel):
    record_id: int
    action: str
    remark: str | None = None


class PoiCreateRequest(BaseModel):
    name: str
    category: str
    tags: list[str]
    longitude: float
    latitude: float
    city: str = "徐州市"
    visit_minutes: int
    indoor: bool = False
    description: str | None = None
    source_channel: str
    collected_at: date


class PoiUpdateRequest(BaseModel):
    poi_id: int
    name: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    longitude: float | None = None
    latitude: float | None = None
    city: str | None = None
    visit_minutes: int | None = None
    indoor: bool | None = None
    description: str | None = None
    source_channel: str | None = None
    collected_at: date | None = None


class PoiStatusRequest(BaseModel):
    status: str


class KnowledgeCreateRequest(BaseModel):
    title: str
    topic: str
    content: str
    source: str


class KnowledgeEditRequest(BaseModel):
    title: str | None = None
    topic: str | None = None
    content: str
    source: str | None = None


class KnowledgeStatusRequest(BaseModel):
    status: str


async def get_current_admin(
    authorization: str | None = Header(default=None),
    session_factory=Depends(get_session_factory),
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise BizError(ErrorCode.ADMIN_UNAUTHORIZED, "管理员未登录或凭证失效")
    payload = decode_admin_jwt(authorization[7:])
    if payload is None or payload.get("role") != "admin":
        raise BizError(ErrorCode.ADMIN_UNAUTHORIZED, "管理员未登录或凭证失效")
    try:
        admin_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise BizError(ErrorCode.ADMIN_UNAUTHORIZED, "管理员未登录或凭证失效")
    async with session_factory() as session:
        user = await UserRepository(session).get(admin_id)
    if user is None:
        raise BizError(ErrorCode.ADMIN_UNAUTHORIZED, "管理员未登录或凭证失效")
    if not user.is_active:
        raise BizError(ErrorCode.FORBIDDEN, "账号已被禁用")
    if user.role != "admin":
        raise BizError(ErrorCode.FORBIDDEN, "无管理端权限")
    return {
        "admin_id": user.id,
        "username": user.username,
        "display_name": user.display_name,
    }


@router.post("/login")
async def admin_login(
    body: AdminLoginRequest,
    session_factory=Depends(get_session_factory),
):
    lock_remaining = _lock_remaining_seconds(body.username)
    if lock_remaining > 0:
        raise BizError(
            ErrorCode.FORBIDDEN,
            f"登录失败次数过多，账号已锁定，请 {lock_remaining // 60 + 1} 分钟后再试",
        )
    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(body.username)
        if user is None or not verify_password(body.password, user.password_hash):
            _record_login_failure(body.username)
            raise BizError(ErrorCode.ADMIN_UNAUTHORIZED, "用户名或密码错误")
        if not user.is_active:
            raise BizError(ErrorCode.FORBIDDEN, "账号已被禁用")
        if user.role != "admin":
            raise BizError(ErrorCode.FORBIDDEN, "无管理端权限")
        _clear_login_failures(body.username)
        token = issue_admin_jwt(user.id, user.username)
        await AuditService(AuditLogRepository(session)).log(
            operator_id=user.id,
            action="admin_login",
            entity_type="user",
            entity_id=user.id,
            after_value={
                "username": user.username,
                "login_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        await session.commit()
    return {
        "token": token,
        "token_type": "Bearer",
        "expires_in": settings.jwt_expire_hours * 3600,
        "username": user.username,
        "display_name": user.display_name,
    }


@router.post("/prices")
async def submit_price(
    body: PriceSubmitRequest,
    admin=Depends(get_current_admin),
    session_factory=Depends(get_session_factory),
):
    if body.price_type not in PRICE_TYPES:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            f"price_type 必须是 {'/'.join(PRICE_TYPES)} 之一",
        )
    async with session_factory() as session:
        poi_repo = PoiRepository(session)
        if await poi_repo.get(body.poi_id) is None:
            raise BizError(ErrorCode.NOT_FOUND, "POI 不存在", detail={"poi_id": body.poi_id})
        service = PriceService(
            PriceRepository(session),
            AuditService(AuditLogRepository(session)),
        )
        record = await service.submit(
            poi_id=body.poi_id,
            price_type=body.price_type,
            amount=body.amount,
            source_channel=body.source_channel,
            collected_at=body.collected_at,
            valid_until=body.valid_until,
            operator_id=admin["admin_id"],
        )
        await session.commit()
    return {"record": to_price_dto(record)}


@router.post("/prices/review")
async def review_price(
    body: PriceReviewRequest,
    admin=Depends(get_current_admin),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        price_repo = PriceRepository(session)
        audit_service = AuditService(AuditLogRepository(session))
        event_repo = ChangeEventRepository(session)
        itinerary_repo = ItineraryRepository(session)

        async def affected_finder(poi_id: int) -> list[int]:
            rows = await itinerary_repo.list_referencing(poi_id)
            return [row.id for row in rows]

        async def event_appender(**kwargs) -> None:
            await event_repo.append(**kwargs)

        service = PriceService(price_repo, audit_service, affected_finder=affected_finder)
        result = await service.review(
            record_id=body.record_id,
            action=body.action,
            reviewer_id=admin["admin_id"],
            remark=body.remark,
            event_appender=event_appender,
        )
        await session.commit()
    suggestions = []
    if result["took_effect"]:
        poi_id = int(result["record"]["poi_id"])
        await invalidate_price_cache(poi_id)
        suggestions = await SuggestionService().on_price_change(
            session_factory,
            poi_id,
            float(result["record"]["amount"]),
            result["affected_itinerary_ids"],
        )
    result["suggestions"] = suggestions
    return result


@router.post("/pois")
async def create_poi(
    body: PoiCreateRequest,
    admin=Depends(get_current_admin),
    session_factory=Depends(get_session_factory),
):
    _validate_poi_invariants(
        name=body.name,
        category=body.category,
        tags=body.tags,
        longitude=body.longitude,
        latitude=body.latitude,
        visit_minutes=body.visit_minutes,
    )
    async with session_factory() as session:
        repo = PoiRepository(session)
        existing = await repo.get_by_name(body.name)
        if existing is not None:
            raise BizError(
                ErrorCode.DATA_CONFLICT,
                f"POI 名称「{body.name}」已存在",
                detail={"poi_id": str(existing.id)},
            )
        poi = Poi(
            name=body.name,
            category=body.category,
            tags=body.tags,
            longitude=body.longitude,
            latitude=body.latitude,
            city=body.city,
            visit_minutes=body.visit_minutes,
            indoor=body.indoor,
            status="有效",
            description=body.description,
            source_channel=body.source_channel,
            collected_at=body.collected_at,
        )
        poi = await repo.add(poi)
        await AuditService(AuditLogRepository(session)).log(
            operator_id=admin["admin_id"],
            action="poi_create",
            entity_type="poi",
            entity_id=poi.id,
            after_value=_poi_dto(poi),
        )
        await session.commit()
    await invalidate_poi_cache()
    return {"poi": _poi_dto(poi)}


@router.patch("/pois")
async def update_poi(
    body: PoiUpdateRequest,
    admin=Depends(get_current_admin),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        repo = PoiRepository(session)
        poi = await repo.get(body.poi_id)
        if poi is None:
            raise BizError(ErrorCode.NOT_FOUND, "POI 不存在", detail={"poi_id": body.poi_id})
        before = _poi_dto(poi)
        if body.name is not None and body.name != poi.name:
            existing = await repo.get_by_name(body.name)
            if existing is not None and existing.id != poi.id:
                raise BizError(
                    ErrorCode.DATA_CONFLICT,
                    f"POI 名称「{body.name}」已存在",
                    detail={"poi_id": str(existing.id)},
                )
            poi.name = body.name
        for field in (
            "category",
            "tags",
            "longitude",
            "latitude",
            "city",
            "visit_minutes",
            "indoor",
            "description",
            "source_channel",
            "collected_at",
        ):
            value = getattr(body, field)
            if value is not None:
                setattr(poi, field, value)
        _validate_poi_invariants(
            name=poi.name,
            category=poi.category,
            tags=list(poi.tags),
            longitude=float(poi.longitude),
            latitude=float(poi.latitude),
            visit_minutes=poi.visit_minutes,
        )

        await AuditService(AuditLogRepository(session)).log(
            operator_id=admin["admin_id"],
            action="poi_update",
            entity_type="poi",
            entity_id=poi.id,
            before_value=before,
            after_value=_poi_dto(poi),
        )
        await session.commit()
    await invalidate_poi_cache()
    return {"poi": _poi_dto(poi)}


@router.patch("/pois/{poi_id}/status")
async def set_poi_status(
    poi_id: int,
    body: PoiStatusRequest,
    admin=Depends(get_current_admin),
    session_factory=Depends(get_session_factory),
):
    if body.status not in ("有效", "失效"):
        raise BizError(ErrorCode.PARAM_INVALID, "status 必须是 有效 或 失效")
    async with session_factory() as session:
        repo = PoiRepository(session)
        poi = await repo.get(poi_id)
        if poi is None:
            raise BizError(ErrorCode.NOT_FOUND, "POI 不存在", detail={"poi_id": poi_id})
        before = {"status": poi.status}
        poi.status = body.status
        affected = [
            row.id
            for row in await ItineraryRepository(session).list_referencing(poi_id)
        ]
        await ChangeEventRepository(session).append(
            event_type="POI状态变更",
            entity_type="poi",
            entity_id=poi_id,
            payload={
                "status": body.status,
                "operator": admin["username"],
            },
            affected_itinerary_ids=affected,
        )
        await AuditService(AuditLogRepository(session)).log(
            operator_id=admin["admin_id"],
            action="poi_status_change",
            entity_type="poi",
            entity_id=poi_id,
            before_value=before,
            after_value={"status": body.status},
        )
        await session.commit()
    await invalidate_poi_cache()
    suggestions = []
    if body.status == "失效":
        suggestions = await SuggestionService().on_poi_closed(
            session_factory, poi_id, affected
        )
    return {
        "poi_id": str(poi_id),
        "status": body.status,
        "affected_itinerary_ids": affected,
        "suggestions": suggestions,
    }


@router.post("/knowledge")
async def upload_knowledge(
    body: KnowledgeCreateRequest,
    admin=Depends(get_current_admin),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        service = KnowledgeService(
            KnowledgeRepository(session),
            KnowledgeChunkRepository(session),
            AuditService(AuditLogRepository(session)),
        )
        result = await service.ingest(
            session,
            title=body.title,
            topic=body.topic,
            content=body.content,
            source=body.source,
            operator_id=admin["admin_id"],
        )
        await session.commit()
    return result


@router.patch("/knowledge/{document_id}")
async def edit_knowledge(
    document_id: int,
    body: KnowledgeEditRequest,
    admin=Depends(get_current_admin),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        repo = KnowledgeRepository(session)
        document = await repo.get(document_id)
        if document is None:
            raise BizError(
                ErrorCode.NOT_FOUND,
                "知识文档不存在",
                detail={"document_id": document_id},
            )
        service = KnowledgeService(
            repo,
            KnowledgeChunkRepository(session),
            AuditService(AuditLogRepository(session)),
        )
        result = await service.ingest(
            session,
            title=body.title or document.title,
            topic=body.topic or document.topic,
            content=body.content,
            source=body.source or document.source,
            operator_id=admin["admin_id"],
        )
        await session.commit()
    return result


@router.patch("/knowledge/{document_id}/status")
async def set_knowledge_status(
    document_id: int,
    body: KnowledgeStatusRequest,
    admin=Depends(get_current_admin),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        service = KnowledgeService(
            KnowledgeRepository(session),
            KnowledgeChunkRepository(session),
            AuditService(AuditLogRepository(session)),
        )
        result = await service.set_status(
            document_id=document_id,
            status=body.status,
            operator_id=admin["admin_id"],
        )
        await session.commit()
    return result


@router.get("/knowledge")
async def list_knowledge(
    topic: str | None = None,
    status: str | None = None,
    admin=Depends(get_current_admin),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        service = KnowledgeService(
            KnowledgeRepository(session),
            KnowledgeChunkRepository(session),
            AuditService(AuditLogRepository(session)),
        )
        documents = await service.list_documents(topic=topic, status=status)
    return {"items": documents, "total": len(documents)}


@router.get("/knowledge/versions")
async def knowledge_versions(
    title: str,
    admin=Depends(get_current_admin),
    session_factory=Depends(get_session_factory),
):
    if not title:
        raise BizError(ErrorCode.PARAM_INVALID, "title 必填")
    async with session_factory() as session:
        service = KnowledgeService(
            KnowledgeRepository(session),
            KnowledgeChunkRepository(session),
            AuditService(AuditLogRepository(session)),
        )
        versions = await service.list_versions(title)
    return {"items": versions, "total": len(versions)}


@router.get("/knowledge/{document_id}")
async def get_knowledge(
    document_id: int,
    admin=Depends(get_current_admin),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        document = await KnowledgeRepository(session).get(document_id)
        if document is None:
            raise BizError(
                ErrorCode.NOT_FOUND,
                "知识文档不存在",
                detail={"document_id": document_id},
            )
        return {
            "document_id": document.id,
            "title": document.title,
            "topic": document.topic,
            "version": document.version,
            "status": document.status,
            "content": document.content,
            "source": document.source,
            "ingested_at": document.ingested_at.isoformat() if document.ingested_at else None,
        }


@router.get("/audits")
async def list_audits(
    operator_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    admin=Depends(get_current_admin),
    session_factory=Depends(get_session_factory),
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise BizError(ErrorCode.PARAM_INVALID, "分页参数非法（page≥1，1≤page_size≤100）")
    since_naive = _to_naive(since) if since is not None else None
    until_naive = _to_naive(until) if until is not None else None
    async with session_factory() as session:
        repo = AuditLogRepository(session)
        logs, total = await repo.list_filtered(
            operator_id=operator_id,
            entity_type=entity_type,
            entity_id=entity_id,
            since=since_naive,
            until=until_naive,
            page=page,
            page_size=page_size,
        )
        items = [_audit_log_dto(log) for log in logs]
    return {"items": items, "total": total, "page": page, "page_size": page_size}