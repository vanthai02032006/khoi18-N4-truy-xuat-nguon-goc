"""Router kiểm tra trạng thái hệ thống.

Sprint 1 chỉ có duy nhất endpoint ``GET /health`` để xác nhận backend đã
chạy thành công và trả về đúng JSON phục vụ demo.
"""

from fastapi import APIRouter, status

from app.schemas import HealthResponse

router = APIRouter(
    tags=["System"],
)


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Kiểm tra hệ thống",
    description="Trả về trạng thái hoạt động của backend. Dùng cho health check khi deploy.",
)
def health_check() -> HealthResponse:
    """Endpoint health check.

    Returns:
        HealthResponse: ``{"status": "running"}`` nếu API đang hoạt động.
    """
    return HealthResponse(status="running")
