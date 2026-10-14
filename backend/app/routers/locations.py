"""Router quản lý **địa điểm / cửa hàng trong hệ thống của một tổ chức** (S-18).

Bối cảnh nghiệp vụ: nhà phân phối có nhiều địa điểm (cửa hàng, kho, điểm bán).
Khi một lô hàng được chuyển về một địa điểm, lô phải ghi rõ **địa điểm hiện tại**
để khi cần thu hồi thì chỉ nhắm đúng cửa hàng thay vì rà soát cả chuỗi cung ứng.

- ``POST /locations``                   : khai báo địa điểm mới (tên + địa chỉ) -
  địa điểm **thuộc tổ chức của người gọi** (lấy từ ``X-Organization-Id``).
- ``GET  /locations``                   : chỉ trả về địa điểm của **chính tổ chức
  đang gọi** - tổ chức khác không nhìn thấy (cách ly đa tổ chức).
- ``GET  /locations/{location_id}/batches``: tra cứu nhanh "lô nào đang nằm ở địa
  điểm này" kèm tổng khối lượng - dữ liệu để phát lệnh thu hồi đúng điểm bán.

**Phân quyền:** cả ba endpoint dùng ``require_farmer`` (farmer hoặc admin, chưa
đăng nhập nhận ``401``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, status

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Batch, Location, User
from app.schemas import (
    BatchResponse,
    LocationBatchesResponse,
    LocationCreate,
    LocationResponse,
)
from app.security import require_farmer
from app.tenant import require_tenant_context

router = APIRouter(
    prefix="/locations",
    tags=["Locations"],
)


def _get_owned_location(db: Session, location_id: int, organization: str) -> Location:
    """Lấy địa điểm theo ``id`` và bảo đảm địa điểm thuộc đúng tổ chức đang gọi.

    Args:
        db: Session SQLAlchemy hiện tại.
        location_id: ID địa điểm cần tra cứu.
        organization: Tổ chức của người gọi (từ ``X-Organization-Id``).

    Returns:
        Location: Địa điểm thuộc tổ chức đang gọi.

    Raises:
        HTTPException: **404** nếu không có địa điểm với ``location_id``;
            **403** nếu địa điểm tồn tại nhưng thuộc tổ chức khác (không xem chéo
            dữ liệu của tổ chức khác).
    """
    location = db.get(Location, location_id)
    if location is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy địa điểm có id={location_id}.",
        )
    if location.organization != organization:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Địa điểm #{location_id} thuộc tổ chức '{location.organization}', "
                "bạn không có quyền truy cập."
            ),
        )
    return location


@router.post(
    "",
    response_model=LocationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Khai báo địa điểm / cửa hàng của tổ chức",
    description=(
        "Khai báo một địa điểm mới gồm **tên** và **địa chỉ**. Địa điểm tự động "
        "thuộc tổ chức gửi request (`X-Organization-Id`), client **không** gửi "
        "`organization`.\n\n"
        "**Chống trùng lặp:** tên địa điểm là duy nhất trong một tổ chức - khai "
        "báo trùng tên trả `409 Conflict` (hai tổ chức khác nhau có thể dùng cùng "
        "một tên địa điểm)."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Đã đăng nhập nhưng sai vai trò."},
        status.HTTP_409_CONFLICT: {"description": "Tên địa điểm đã tồn tại trong tổ chức."},
    },
)
def create_location(
    payload: LocationCreate,
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> Location:
    """Khai báo địa điểm mới thuộc tổ chức của người gọi.

    Args:
        payload: Tên và địa chỉ địa điểm (đã được Pydantic chuẩn hoá ``strip``).
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        current_org: Tổ chức của người gọi - gán làm chủ sở hữu địa điểm.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        Location: Bản ghi địa điểm vừa tạo (HTTP 201).

    Raises:
        HTTPException: 401 nếu chưa đăng nhập; 403 nếu sai vai trò; 409 nếu tên
            địa điểm đã tồn tại trong tổ chức; 500 nếu ghi database thất bại.
    """
    _ = current_user

    duplicated = db.scalar(
        select(Location).where(
            Location.organization == current_org,
            Location.name == payload.name,
        )
    )
    if duplicated is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Địa điểm '{payload.name}' đã tồn tại trong tổ chức '{current_org}'."
            ),
        )

    location = Location(
        organization=current_org,
        name=payload.name,
        address=payload.address,
    )
    db.add(location)

    try:
        db.commit()
    except IntegrityError as exc:
        # Tranh chấp hiếm gặp: tên vừa bị request khác tạo trước khi commit.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Địa điểm '{payload.name}' đã tồn tại trong tổ chức '{current_org}'."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể lưu địa điểm vào cơ sở dữ liệu.",
        ) from exc

    db.refresh(location)
    return location


@router.get(
    "",
    response_model=list[LocationResponse],
    status_code=status.HTTP_200_OK,
    summary="Danh sách địa điểm của tổ chức đang gọi",
    description=(
        "Trả về **toàn bộ địa điểm của chính tổ chức đang gọi** (sắp xếp theo tên "
        "A→Z). Địa điểm của tổ chức khác không xuất hiện trong kết quả."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Đã đăng nhập nhưng sai vai trò."},
    },
)
def list_locations(
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> list[Location]:
    """Liệt kê địa điểm của tổ chức đang gọi (không lộ dữ liệu tổ chức khác).

    Args:
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        current_org: Tổ chức của người gọi - dùng làm điều kiện lọc.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        list[Location]: Danh sách địa điểm của tổ chức (rỗng nếu chưa khai báo).

    Raises:
        HTTPException: 401 nếu chưa đăng nhập; 403 nếu sai vai trò.
    """
    _ = current_user
    return list(
        db.scalars(
            select(Location)
            .where(Location.organization == current_org)
            .order_by(Location.name)
        ).all()
    )


@router.get(
    "/{location_id}/batches",
    response_model=LocationBatchesResponse,
    status_code=status.HTTP_200_OK,
    summary="Tra cứu lô đang nằm tại một địa điểm (phục vụ thu hồi)",
    description=(
        "Trả về danh sách lô **hiện đang nằm tại địa điểm** kèm tổng số lô và tổng "
        "khối lượng - đủ dữ liệu để phát lệnh thu hồi đúng cửa hàng thay vì rà "
        "soát cả chuỗi.\n\n"
        "**Cách ly đa tổ chức:** chỉ tra cứu được địa điểm của chính tổ chức "
        "đang gọi; địa điểm của tổ chức khác trả `403 Forbidden`."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Địa điểm thuộc tổ chức khác."},
        status.HTTP_404_NOT_FOUND: {"description": "Không tìm thấy địa điểm."},
    },
)
def list_batches_at_location(
    location_id: int = Path(..., ge=1, description="ID địa điểm cần tra cứu."),
    current_user: User = Depends(require_farmer),
    current_org: str = Depends(require_tenant_context),
    db: Session = Depends(get_db),
) -> LocationBatchesResponse:
    """Liệt kê các lô hiện có mặt tại địa điểm của tổ chức đang gọi.

    Args:
        location_id: ID địa điểm cần tra cứu.
        current_user: Tài khoản đã đăng nhập (farmer hoặc admin).
        current_org: Tổ chức của người gọi - dùng để kiểm tra quyền sở hữu địa điểm.
        db: Session SQLAlchemy từ dependency ``get_db``.

    Returns:
        LocationBatchesResponse: Địa điểm, tổng số lô, tổng khối lượng và danh
        sách lô đang nằm tại đó (HTTP 200).

    Raises:
        HTTPException: 401 nếu chưa đăng nhập; 403 nếu sai vai trò hoặc địa điểm
            thuộc tổ chức khác; 404 nếu không tìm thấy địa điểm.
    """
    _ = current_user
    location = _get_owned_location(db, location_id, current_org)

    batches = list(
        db.scalars(
            select(Batch)
            .where(Batch.current_location_id == location.id)
            .order_by(Batch.id)
        ).all()
    )
    total_quantity = round(sum(batch.quantity for batch in batches), 4)

    return LocationBatchesResponse(
        location=LocationResponse.model_validate(location),
        total_batches=len(batches),
        total_quantity=total_quantity,
        batches=[BatchResponse.model_validate(batch) for batch in batches],
    )
