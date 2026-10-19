"""Router quản lý chuyến xe vận chuyển nông sản trong chuỗi lạnh (Trips).

Hỗ trợ:
- POST /trips: Tạo chuyến xe mới, kiểm tra quyền multi-tenant của các lô hàng,
  tự động ghi sự kiện BATCH_LOADED_TO_TRIP vào chuỗi SHA-256 Hash-Chain của từng lô hàng.
- GET /trips: Lấy danh sách chuyến xe (cô lập theo tổ chức).
- GET /trips/{id}: Lấy thông tin chi tiết một chuyến xe.
- PATCH /trips/{id}/status: Cập nhật trạng thái chuyến xe (in_transit -> completed),
  khi kết thúc chuyến xe tự động ghi sự kiện BATCH_UNLOADED_FROM_TRIP vào chuỗi Hash-Chain.
"""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.events import record_event
from app.models import (
    Batch,
    EVENT_TYPE_BATCH_LOADED_TO_TRIP,
    EVENT_TYPE_BATCH_UNLOADED_FROM_TRIP,
    TRIP_STATUS_COMPLETED,
    TRIP_STATUS_IN_TRANSIT,
    Trip,
    TripBatch,
    User,
)
from app.schemas import TripCreate, TripResponse, TripStatusUpdate
from app.security import require_farmer
from app.tenant import require_tenant_context

router = APIRouter(
    prefix="/trips",
    tags=["Vận Chuyển & Giám Sát Chuỗi Lạnh"],
)


def _serialize_trip(trip: Trip) -> dict[str, Any]:
    """Chuyển đổi Trip ORM model sang dict phản hồi API kèm thông tin lô hàng."""
    batch_ids = [tb.batch_id for tb in trip.trip_batches]
    batches_data = []
    for tb in trip.trip_batches:
        b = tb.batch
        if b:
            batches_data.append({
                "batch_id": b.id,
                "batch_code": b.batch_code or f"LOT-{b.id}",
                "product_name": b.product_name,
                "quantity": b.quantity,
                "current_holder_org": b.current_holder_org,
            })
    return {
        "id": trip.id,
        "code": trip.code,
        "container_code": trip.container_code,
        "vehicle_plate": trip.vehicle_plate,
        "driver_name": trip.driver_name,
        "status": trip.status,
        "organization": trip.organization,
        "created_at": trip.created_at,
        "updated_at": trip.updated_at,
        "batch_ids": batch_ids,
        "batches": batches_data,
    }


@router.post(
    "",
    response_model=TripResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo chuyến xe mới & ghi nhận sự kiện xếp lô lên xe",
)
def create_trip(
    payload: TripCreate,
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Khai báo chuyến xe vận chuyển mới.

    - Kiểm tra mã chuyến không trùng lặp.
    - Kiểm tra quyền sở hữu các lô hàng theo Multi-tenant.
    - Tự động ghi sự kiện BATCH_LOADED_TO_TRIP vào chuỗi băm SHA-256 của từng lô hàng.
    """
    clean_code = payload.code.strip()
    exists = db.scalar(select(Trip).where(Trip.code == clean_code))
    if exists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mã chuyến xe '{clean_code}' đã tồn tại trong hệ thống.",
        )

    # Kiểm tra các lô hàng và phân quyền Multi-tenant
    batches_to_load: list[Batch] = []
    if payload.batch_ids:
        for bid in payload.batch_ids:
            b = db.get(Batch, bid)
            if b is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Không tìm thấy lô hàng ID #{bid}.",
                )
            # Kiểm tra phân quyền đa tổ chức (Multi-tenant):
            # Nếu không phải admin hoặc tổ chức mặc định toàn quyền thì không được can thiệp lô của tổ chức khác
            if current_org and current_org != "HTX Nông Nghiệp Số 4" and current_user.role != "admin":
                holder = b.current_holder_org or (b.farm.owner if b.farm else None)
                if holder and holder != current_org and current_org not in holder:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Lô hàng #{bid} đang do tổ chức khác ({holder}) quản lý. Bạn không có quyền xếp lô này lên chuyến xe của {current_org}.",
                    )
            batches_to_load.append(b)

    trip = Trip(
        code=clean_code,
        container_code=payload.container_code.strip() if payload.container_code else None,
        vehicle_plate=payload.vehicle_plate.strip(),
        driver_name=payload.driver_name.strip(),
        status=TRIP_STATUS_IN_TRANSIT,
        organization=current_org,
    )
    db.add(trip)
    db.flush()

    for b in batches_to_load:
        tb = TripBatch(trip_id=trip.id, batch_id=b.id)
        db.add(tb)

        # Ghi sự kiện BATCH_LOADED_TO_TRIP vào Hash-Chain của lô hàng
        record_event(
            db,
            batch_id=b.id,
            event_type=EVENT_TYPE_BATCH_LOADED_TO_TRIP,
            actor=current_user.username,
            organization=current_org,
            payload={
                "trip_id": trip.id,
                "trip_code": trip.code,
                "container_code": trip.container_code,
                "vehicle_plate": trip.vehicle_plate,
                "driver_name": trip.driver_name,
                "note": f"Xếp lô vào thùng lạnh chuyến xe {trip.code} (Biển số: {trip.vehicle_plate}, Tài xế: {trip.driver_name})",
            },
        )

    db.commit()
    db.refresh(trip)
    return _serialize_trip(trip)


@router.get(
    "",
    response_model=list[TripResponse],
    summary="Lấy danh sách các chuyến xe (cô lập theo tổ chức)",
)
def list_trips(
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Danh sách chuyến xe thuộc tổ chức của người dùng hiện tại."""
    stmt = select(Trip).order_by(Trip.id.desc())
    if current_org and current_org != "HTX Nông Nghiệp Số 4" and current_user.role != "admin":
        stmt = stmt.where(Trip.organization == current_org)

    trips = list(db.scalars(stmt).all())
    return [_serialize_trip(t) for t in trips]


@router.get(
    "/{trip_id}",
    response_model=TripResponse,
    summary="Lấy chi tiết một chuyến xe",
)
def get_trip(
    trip_id: int = Path(..., gt=0),
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Xem chi tiết một chuyến xe."""
    trip = db.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy chuyến xe có ID #{trip_id}.",
        )

    if current_org and current_org != "HTX Nông Nghiệp Số 4" and current_user.role != "admin":
        if trip.organization != current_org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền truy cập thông tin chuyến xe của tổ chức khác.",
            )

    return _serialize_trip(trip)


@router.patch(
    "/{trip_id}/status",
    response_model=TripResponse,
    summary="Cập nhật trạng thái chuyến xe & ghi nhận kết thúc chuyến",
)
def update_trip_status(
    trip_id: int,
    payload: TripStatusUpdate,
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Cập nhật trạng thái chuyến xe (in_transit -> completed).

    Khi trạng thái là 'completed', hệ thống tự động ghi nhận sự kiện
    BATCH_UNLOADED_FROM_TRIP vào chuỗi SHA-256 Hash-Chain của tất cả lô hàng trên xe.
    """
    trip = db.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy chuyến xe có ID #{trip_id}.",
        )

    if current_org and current_org != "HTX Nông Nghiệp Số 4" and current_user.role != "admin":
        if trip.organization != current_org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền can thiệp vào chuyến xe của tổ chức khác.",
            )

    new_status = payload.status.strip()
    trip.status = new_status

    if new_status == TRIP_STATUS_COMPLETED:
        for tb in trip.trip_batches:
            record_event(
                db,
                batch_id=tb.batch_id,
                event_type=EVENT_TYPE_BATCH_UNLOADED_FROM_TRIP,
                actor=current_user.username,
                organization=current_org,
                payload={
                    "trip_id": trip.id,
                    "trip_code": trip.code,
                    "container_code": trip.container_code,
                    "vehicle_plate": trip.vehicle_plate,
                    "driver_name": trip.driver_name,
                    "status": "completed",
                    "note": f"Dỡ hàng khỏi thùng lạnh khi chuyến xe {trip.code} hoàn thành hành trình.",
                },
            )

    db.commit()
    db.refresh(trip)
    return _serialize_trip(trip)
