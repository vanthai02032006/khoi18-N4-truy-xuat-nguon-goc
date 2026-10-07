"""Router truy xuất nguồn gốc công khai dành cho Người tiêu dùng (Story 6).

Đặc điểm:
- Không yêu cầu đăng nhập (Không có token hay HTTP Basic).
- Phục vụ người mua hàng quét mã QR trên bao bì sản phẩm (cỡ tem 3x3 cm).
- Chỉ hiển thị các thông tin minh bạch an toàn, giấu toàn bộ thông tin nhạy cảm.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Batch

router = APIRouter(
    prefix="/public",
    tags=["Public Traceability"],
)


class PublicTraceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    batch_id: int
    product_name: str
    harvest_date: str
    farm_name: str
    farm_location: str
    farm_owner: str
    organization: str
    status: str
    trace_url: str


@router.get(
    "/trace/{batch_id}",
    response_model=PublicTraceResponse,
    status_code=status.HTTP_200_OK,
    summary="Cổng tra cứu công khai cho người tiêu dùng",
    description="Tra cứu nguồn gốc lô hàng bằng mã QR không cần đăng nhập.",
)
def get_public_trace(
    batch_id: int = Path(..., description="ID của lô nông sản", ge=1),
    db: Session = Depends(get_db),
) -> PublicTraceResponse:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy thông tin lô hàng này trong hệ thống.",
        )

    return PublicTraceResponse(
        batch_id=batch.id,
        product_name=batch.product_name,
        harvest_date=batch.harvest_date.isoformat(),
        farm_name=batch.farm.name if batch.farm else "Vùng trồng VietGAP",
        farm_location=batch.farm.location if batch.farm else "Chưa rõ",
        farm_owner=batch.farm.owner if batch.farm else "Nông hộ liên kết",
        organization=batch.organization,
        status=batch.status,
        trace_url=f"/public/trace/{batch.id}",
    )
