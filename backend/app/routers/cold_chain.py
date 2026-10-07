"""Router giám sát chuỗi lạnh và phát hiện vi phạm nhiệt độ (S-41)."""

from fastapi import APIRouter, status
from app.cold_chain import (
    ColdChainAnalysisRequest,
    ColdChainAnalysisResponse,
    analyze_cold_chain,
)

router = APIRouter(
    prefix="/cold-chain",
    tags=["Giám sát Chuỗi Lạnh (Cold Chain Monitoring - S-41)"],
)


@router.post(
    "/analyze",
    response_model=ColdChainAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Phân tích chuỗi thời điểm và nhiệt độ, phát hiện vi phạm (S-41)",
    description=(
        "Endpoint nhận chuỗi thời điểm và nhiệt độ đo được (time-series), đối chiếu với ngưỡng an toàn [min_temp, max_temp] "
        "để phát hiện các lần vi phạm, thời lượng vượt ngưỡng, đỉnh nhiệt độ, và xử lý vượt qua nửa đêm."
    ),
)
def analyze_temperature_readings(
    payload: ColdChainAnalysisRequest,
) -> ColdChainAnalysisResponse:
    """Phân tích vi phạm nhiệt độ chuỗi lạnh."""
    result = analyze_cold_chain(
        readings=payload.readings,
        min_temp=payload.min_temp,
        max_temp=payload.max_temp,
        min_duration_minutes=payload.min_duration_minutes,
    )
    return ColdChainAnalysisResponse(**result)
