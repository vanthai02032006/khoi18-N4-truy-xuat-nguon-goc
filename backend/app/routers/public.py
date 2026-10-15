"""Router tra cứu nguồn gốc nông sản công khai dành cho người tiêu dùng (SCRUM-128).

Tiêu chí nghiệm thu (DoD / AC):
1. Không yêu cầu đăng nhập: người tiêu dùng quét mã QR hoặc nhập mã tra cứu được ngay.
2. Hiển thị đầy đủ thông tin: tên vùng trồng, tỉnh/địa phương, ngày thu hoạch.
3. Các mốc sơ chế, vận chuyển, phân phối theo thứ tự thời gian.
4. Lô gộp từ nhiều nguồn: hiện đầy đủ mọi vùng trồng xuất xứ gốc (truy vết phả hệ DAG).
5. Mã không tồn tại: trả về HTTP 404 "Không tìm thấy thông tin lô hàng này trong hệ thống",
   tuyệt đối không để lộ thông tin nhạy cảm hay thông tin nội bộ nào khác.
"""
from __future__ import annotations

import json
from collections import deque
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Batch

router = APIRouter(
    prefix="/public",
    tags=["Public Traceability"],
)


class OriginFarmItem(BaseModel):
    batch_code: str
    farm_name: str
    farm_location: str
    farm_province: str
    farm_owner: str


class TimelineEventItem(BaseModel):
    id: int
    event_type: str
    event_type_display: str
    timestamp: str
    organization: str
    actor: str
    description: str


class PublicTraceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    batch_id: int
    batch_code: str
    product_name: str
    harvest_date: str
    farm_name: str
    farm_location: str
    farm_province: str
    farm_owner: str
    organization: str
    status: str
    quantity: float
    remaining_quantity: Optional[float] = None
    is_merged: bool
    origin_farms: list[OriginFarmItem]
    events: list[TimelineEventItem]
    trace_url: str


def _extract_province(location: str) -> str:
    """Trích xuất tên tỉnh/thành phố từ chuỗi địa chỉ."""
    if not location:
        return "Chưa rõ"
    parts = [p.strip() for p in location.split(",") if p.strip()]
    if not parts:
        return location
    last = parts[-1]
    for prefix in ["Tỉnh ", "Thành phố ", "TP.", "TP "]:
        if last.startswith(prefix):
            return last[len(prefix):].strip()
    return last


def _format_event_display(event_type: str) -> str:
    """Chuyển mã sự kiện nội bộ thành tên tiếng Việt thân thiện cho người tiêu dùng."""
    mapping = {
        "HARVEST": "Thu hoạch tại vùng trồng",
        "BATCH_CREATED": "Khởi tạo lô thu hoạch gốc",
        "PREPROCESSING": "Sơ chế & làm sạch nông sản",
        "SORTING": "Phân loại tiêu chuẩn chất lượng",
        "PACKAGING": "Đóng gói bao bì & dán mã QR",
        "PACKING": "Đóng thùng & hoàn thiện bao bì",
        "COLD_STORAGE": "Nhập kho lưu trữ & bảo quản chuỗi lạnh",
        "TEMPERATURE_MONITORING": "Giám sát nhiệt độ chuỗi lạnh",
        "HANDOVER_PENDING": "Khởi tạo biên bản bàn giao vận chuyển",
        "HANDOVER_ACCEPTED": "Xác nhận nhận hàng & chuyển giao chuỗi cung ứng",
        "TRANSPORT": "Vận chuyển hàng hóa",
        "QUALITY_INSPECTION": "Kiểm định an toàn thực phẩm",
        "INSPECTION_ORDER": "Thanh tra chất lượng nông sản",
        "DISTRIBUTION": "Phân phối tới siêu thị / chuỗi bán lẻ",
        "RETAIL": "Bày bán tại kệ hàng tiêu dùng",
        "BATCH_SPLIT": "Tách lô phục vụ phân phối",
        "BATCH_MERGE": "Gộp lô phối trộn thành phẩm",
    }
    return mapping.get(event_type.upper(), event_type.replace("_", " ").title())


def _parse_event_description(payload: str) -> str:
    """Đọc mô tả tóm tắt từ payload JSON của sự kiện."""
    if not payload:
        return "Ghi nhận sự kiện chuỗi cung ứng."
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            for k in ["note", "description", "reason", "details", "message"]:
                if k in data and data[k]:
                    return str(data[k])
            items = [f"{k}: {v}" for k, v in data.items() if not k.startswith("_") and not isinstance(v, (dict, list))]
            if items:
                return "; ".join(items[:3])
    except Exception:
        pass
    return payload.strip() or "Ghi nhận sự kiện chuỗi cung ứng."


def _find_all_origin_farms(db: Session, batch: Batch) -> list[OriginFarmItem]:
    """Tìm toàn bộ các vùng trồng xuất xứ gốc (Root Harvest Farms) bằng thuật toán duyệt phả hệ."""
    visited_batch_ids = {batch.id}
    queue = deque([batch])
    root_batches: list[Batch] = []
    has_parents = False

    while queue:
        curr = queue.popleft()
        curr_parents: list[Batch] = []

        if curr.parent_relations:
            for rel in curr.parent_relations:
                p = db.get(Batch, rel.parent_batch_id)
                if p and p.id not in visited_batch_ids:
                    visited_batch_ids.add(p.id)
                    curr_parents.append(p)
        elif curr.parent_id:
            p = db.get(Batch, curr.parent_id)
            if p and p.id not in visited_batch_ids:
                visited_batch_ids.add(p.id)
                curr_parents.append(p)

        if curr_parents:
            has_parents = True
            queue.extend(curr_parents)
        else:
            root_batches.append(curr)

    if not has_parents:
        root_batches = [batch]

    seen = set()
    result: list[OriginFarmItem] = []
    for rb in root_batches:
        f = rb.farm
        if f:
            key = (f.name, f.location)
            if key not in seen:
                seen.add(key)
                result.append(
                    OriginFarmItem(
                        batch_code=rb.batch_code or f"LOT-{rb.id:04d}",
                        farm_name=f.name,
                        farm_location=f.location,
                        farm_province=_extract_province(f.location),
                        farm_owner=f.owner,
                    )
                )

    if not result:
        result.append(
            OriginFarmItem(
                batch_code=batch.batch_code or f"LOT-{batch.id:04d}",
                farm_name=batch.farm.name if batch.farm else "Vùng trồng VietGAP",
                farm_location=batch.farm.location if batch.farm else "Chưa rõ",
                farm_province=_extract_province(batch.farm.location if batch.farm else ""),
                farm_owner=batch.farm.owner if batch.farm else "Nông hộ liên kết",
            )
        )
    return result


def _build_public_trace_response(batch: Batch, db: Session) -> PublicTraceResponse:
    origin_farms = _find_all_origin_farms(db, batch)
    is_merged = bool(batch.parent_relations and len(batch.parent_relations) > 1) or len(origin_farms) > 1

    sorted_events = sorted(batch.events, key=lambda e: e.timestamp or "")
    timeline_items = [
        TimelineEventItem(
            id=e.id,
            event_type=e.event_type,
            event_type_display=_format_event_display(e.event_type),
            timestamp=e.timestamp or "",
            organization=e.organization or batch.current_holder_org,
            actor=e.actor or "",
            description=_parse_event_description(e.payload),
        )
        for e in sorted_events
    ]

    farm = batch.farm
    farm_name = farm.name if farm else "Vùng trồng VietGAP"
    farm_location = farm.location if farm else "Chưa rõ"
    farm_province = _extract_province(farm_location)
    farm_owner = farm.owner if farm else "Nông hộ liên kết"

    return PublicTraceResponse(
        batch_id=batch.id,
        batch_code=batch.batch_code or f"LOT-{batch.id:04d}",
        product_name=batch.product_name,
        harvest_date=batch.harvest_date.isoformat(),
        farm_name=farm_name,
        farm_location=farm_location,
        farm_province=farm_province,
        farm_owner=farm_owner,
        organization=batch.organization,
        status=batch.status,
        quantity=batch.quantity,
        remaining_quantity=batch.remaining_quantity or batch.quantity,
        is_merged=is_merged,
        origin_farms=origin_farms,
        events=timeline_items,
        trace_url=f"/public/trace/{batch.batch_code or batch.id}",
    )


@router.get(
    "/trace/{identifier}",
    response_model=PublicTraceResponse,
    status_code=status.HTTP_200_OK,
    summary="Cổng tra cứu nguồn gốc công khai cho người tiêu dùng (SCRUM-128)",
    description="Tra cứu nguồn gốc lô hàng bằng ID hoặc mã lô (batch_code). Không cần đăng nhập.",
)
def get_public_trace(
    identifier: str = Path(..., description="ID số hoặc mã lô nông sản (vd: LOT-20261015-001)"),
    db: Session = Depends(get_db),
) -> PublicTraceResponse:
    batch: Batch | None = None
    if identifier.isdigit():
        batch = db.get(Batch, int(identifier))
    if batch is None:
        batch = db.scalar(select(Batch).where(Batch.batch_code == identifier))

    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy thông tin lô hàng này trong hệ thống.",
        )

    return _build_public_trace_response(batch, db)


@router.get(
    "/trace/by-code/{batch_code}",
    response_model=PublicTraceResponse,
    status_code=status.HTTP_200_OK,
    summary="Tra cứu công khai theo mã lô (SCRUM-128)",
)
def get_public_trace_by_code(
    batch_code: str = Path(..., description="Mã lô nông sản in trên bao bì"),
    db: Session = Depends(get_db),
) -> PublicTraceResponse:
    batch = db.scalar(select(Batch).where(Batch.batch_code == batch_code))
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy thông tin lô hàng này trong hệ thống.",
        )
    return _build_public_trace_response(batch, db)
