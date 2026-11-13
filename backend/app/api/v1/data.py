from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import (
    get_session_factory,
    issue_session_token,
    set_session_cookie,
)
from app.core.errors import BizError, ErrorCode
from app.repositories.knowledge_repo import DataSourceRepository
from app.repositories.poi_repo import (
    PoiHoursRepository,
    PoiRepository,
    PriceRepository,
)
from app.services.itinerary_service import money
from app.services.price_service import PriceService, to_price_dto
from app.services.audit_service import AuditService
from app.repositories.knowledge_repo import AuditLogRepository
from app.tools.live import get_route
from app.tools.pois import Coordinate

router = APIRouter(tags=["data"])


def _price_meta(price) -> dict | None:
    if price is None:
        return None
    return {
        "source_channel": price.source_channel,
        "collected_at": price.collected_at.isoformat(),
        "is_reference": price.valid_until < date.today(),
    }


@router.get("/pois")
async def list_pois(
    request: Request,
    response: Response,
    category: str | None = None,
    tag: str | None = None,
    active: bool = True,
    page: int = 1,
    page_size: int = 20,
    session_factory=Depends(get_session_factory),
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise BizError(ErrorCode.PARAM_INVALID, "分页参数非法（page≥1，1≤page_size≤100）")
    token = issue_session_token(request)
    async with session_factory() as session:
        repo = PoiRepository(session)
        price_repo = PriceRepository(session)
        pois, total = await repo.list_paged(
            category=category,
            tag=tag,
            status="有效" if active else None,
            page=page,
            page_size=page_size,
        )
        items = []
        for poi in pois:
            price = await price_repo.get_current(poi.id)
            items.append(
                {
                    "poi_id": str(poi.id),
                    "name": poi.name,
                    "category": poi.category,
                    "tags": list(poi.tags),
                    "location": {"lng": float(poi.longitude), "lat": float(poi.latitude)},
                    "visit_minutes": poi.visit_minutes,
                    "indoor": poi.indoor,
                    "status": poi.status,
                    "ticket_price": money(price.amount) if price else "0.00",
                    "price_meta": _price_meta(price),
                }
            )
    set_session_cookie(response, token)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/pois/{poi_id}")
async def get_poi(
    poi_id: str,
    request: Request,
    response: Response,
    session_factory=Depends(get_session_factory),
):
    try:
        numeric_id = int(poi_id)
    except ValueError:
        raise BizError(ErrorCode.PARAM_INVALID, "poi_id 必须是数字")
    token = issue_session_token(request)
    async with session_factory() as session:
        poi = await PoiRepository(session).get(numeric_id)
        if poi is None:
            raise BizError(ErrorCode.NOT_FOUND, "POI 不存在")
        hours = await PoiHoursRepository(session).list_week(poi.id)
        price = await PriceRepository(session).get_current(poi.id)
        payload = {
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
            "ticket_price": money(price.amount) if price else "0.00",
            "price_meta": _price_meta(price),
            "source_channel": poi.source_channel,
            "collected_at": poi.collected_at.isoformat(),
            "hours": [
                {
                    "day_of_week": h.day_of_week,
                    "open_time": h.open_time.isoformat() if h.open_time else None,
                    "close_time": h.close_time.isoformat() if h.close_time else None,
                    "closed": h.closed,
                }
                for h in hours
            ],
        }
    set_session_cookie(response, token)
    return payload


def _parse_coordinate(text: str, field: str) -> Coordinate:
    try:
        lng_str, lat_str = text.split(",")
        lng = float(lng_str)
        lat = float(lat_str)
    except (ValueError, TypeError):
        raise BizError(ErrorCode.PARAM_INVALID, f"{field} 格式必须是 lng,lat")
    from app.models.poi import XUZHOU_LAT_MAX, XUZHOU_LAT_MIN, XUZHOU_LON_MAX, XUZHOU_LON_MIN

    if not (XUZHOU_LON_MIN <= lng <= XUZHOU_LON_MAX and XUZHOU_LAT_MIN <= lat <= XUZHOU_LAT_MAX):
        raise BizError(ErrorCode.PARAM_INVALID, "坐标超出徐州市范围")
    return Coordinate(lat=lat, lng=lng)


@router.get("/routes")
async def query_routes(
    request: Request,
    response: Response,
    origin: str,
    destination: str,
    modes: str = "公交,步行,驾车",
    session_factory=Depends(get_session_factory),
):
    origin_point = _parse_coordinate(origin, "origin")
    mode_list = [m.strip() for m in modes.split(",") if m.strip()]
    allowed_modes = ("公交", "步行", "驾车")
    for m in mode_list:
        if m not in allowed_modes:
            raise BizError(ErrorCode.PARAM_INVALID, f"交通方式必须是 {'/'.join(allowed_modes)}")

    token = issue_session_token(request)
    destination_point: Coordinate
    if destination.isdigit():
        async with session_factory() as session:
            poi = await PoiRepository(session).get(int(destination))
            if poi is None:
                raise BizError(ErrorCode.NOT_FOUND, "目的地 POI 不存在")
            destination_point = Coordinate(lat=float(poi.latitude), lng=float(poi.longitude))
    else:
        destination_point = _parse_coordinate(destination, "destination")

    routes = []
    for mode in mode_list or list(allowed_modes):
        result = await get_route(origin_point, destination_point, mode)
        if result.ok and result.data is not None:
            info = result.data
            entry: dict = {"mode": mode}
            if info.unavailable:
                entry.update({"unavailable": True, "reason": info.reason or f"暂无{mode}数据"})
            else:
                entry.update(
                    {
                        "duration_minutes": info.duration_minutes,
                        "distance_meters": info.distance_meters,
                        "cost_meta": {
                            "source_channel": result.meta.source_channel or "高德",
                            "collected_at": info.collected_at.isoformat() if info.collected_at else None,
                            "is_reference": bool(result.meta.degraded or info.degraded),
                        },
                    }
                )
            routes.append(entry)
        else:
            routes.append({"mode": mode, "unavailable": True, "reason": f"暂无{mode}数据"})

    set_session_cookie(response, token)
    return {"routes": routes}


@router.get("/prices/{poi_id}")
async def get_prices(
    poi_id: str,
    request: Request,
    response: Response,
    include_hidden: bool = False,
    session_factory=Depends(get_session_factory),
):
    try:
        numeric_id = int(poi_id)
    except ValueError:
        raise BizError(ErrorCode.PARAM_INVALID, "poi_id 必须是数字")
    token = issue_session_token(request)
    async with session_factory() as session:
        service = PriceService(
            PriceRepository(session),
            AuditService(AuditLogRepository(session)),
        )
        view = await service.get_view(numeric_id, include_hidden=include_hidden)
    set_session_cookie(response, token)
    return view


@router.get("/sources/status")
async def sources_status(
    request: Request,
    response: Response,
    session_factory=Depends(get_session_factory),
):
    token = issue_session_token(request)
    async with session_factory() as session:
        sources = await DataSourceRepository(session).list_all()
        payload = {
            "sources": [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "last_success_at": s.last_success_at.isoformat() if s.last_success_at else None,
                    "availability": s.availability,
                    "last_error": s.last_error,
                }
                for s in sources
            ]
        }
    set_session_cookie(response, token)
    return payload