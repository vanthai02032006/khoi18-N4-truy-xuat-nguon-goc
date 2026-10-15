"""Router quản lý lệnh thu hồi nông sản sự cố (Story 5).

Nghiệp vụ:
- Cảnh báo khẩn cấp (active-alerts) tới nhà phân phối/tổ chức đang giữ lô bị thu hồi.
- Cho phép xác nhận đã xử lý gom hàng khỏi kệ kèm thời điểm và ghi chú thực địa.
- Cán bộ kiểm tra theo dõi tiến độ thu hồi theo thời gian thực (ai đã xong, ai còn PENDING).
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Recall, RecallAssignment, User, ROLE_ADMIN
from app.security import get_current_user

router = APIRouter(
    prefix="/recalls",
    tags=["Recalls"],
)


class ResolveRecallRequest(BaseModel):
    notes: str = Field(..., min_length=1, max_length=500, description="Ghi chú xử lý thực địa (ví dụ: đã niêm phong 50kg)")


class RecallAlertItem(BaseModel):
    recall_id: int
    recall_code: str
    title: str
    reason: str
    assignment_id: int
    batch_id: int
    product_name: str
    status: str
    organization: str


class RecallStatusResponse(BaseModel):
    id: int
    code: str
    title: str
    reason: str
    status: str
    created_at: str
    assignments: list[dict]


@router.get(
    "/active-alerts",
    response_model=list[RecallAlertItem],
    status_code=status.HTTP_200_OK,
    summary="Cảnh báo thu hồi khẩn cấp cho tổ chức đang đăng nhập",
    description="Trả về danh sách các lô thuộc diện thu hồi mà tổ chức hiện tại đang nắm giữ.",
)
def get_active_recall_alerts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[RecallAlertItem]:
    stmt = (
        select(RecallAssignment)
        .join(Recall)
        .where(Recall.status == "ACTIVE")
    )
    if current_user.role != ROLE_ADMIN:
        stmt = stmt.where(RecallAssignment.organization == current_user.organization)

    assignments = db.scalars(stmt).all()
    results = []
    for a in assignments:
        results.append(
            RecallAlertItem(
                recall_id=a.recall_id,
                recall_code=a.recall.code,
                title=a.recall.title,
                reason=a.recall.reason,
                assignment_id=a.id,
                batch_id=a.batch_id,
                product_name=a.batch.product_name if a.batch else "Lô nông sản",
                status=a.status,
                organization=a.organization,
            )
        )
    return results


@router.post(
    "/{recall_id}/assignments/{assignment_id}/resolve",
    status_code=status.HTTP_200_OK,
    summary="Xác nhận đã xử lý thu hồi tại cơ sở",
    description="Nhà phân phối xác nhận đã gom hàng khỏi kệ, lưu vết người thực hiện và thời điểm.",
)
def resolve_recall_assignment(
    recall_id: int = Path(..., ge=1),
    assignment_id: int = Path(..., ge=1),
    payload: ResolveRecallRequest = ...,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assignment = db.get(RecallAssignment, assignment_id)
    if assignment is None or assignment.recall_id != recall_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy mục phân công thu hồi này.",
        )

    # Kiểm tra quyền: chỉ tổ chức được phân công hoặc admin mới được xác nhận
    if current_user.role != ROLE_ADMIN and current_user.organization != assignment.organization:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền xác nhận thay cho tổ chức khác.",
        )

    now = datetime.now(timezone.utc).isoformat()
    assignment.status = "COMPLETED"
    assignment.resolved_by = current_user.username
    assignment.resolved_at = now
    assignment.notes = payload.notes
    db.commit()

    return {
        "message": "Đã xác nhận xử lý thu hồi thành công.",
        "assignment_id": assignment.id,
        "status": assignment.status,
        "resolved_by": assignment.resolved_by,
        "resolved_at": assignment.resolved_at,
        "notes": assignment.notes,
    }


@router.get(
    "/{recall_id}",
    response_model=RecallStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Xem chi tiết tiến độ lệnh thu hồi",
    description="Cán bộ kiểm tra theo dõi tiến độ của từng điểm phân phối (PENDING/COMPLETED).",
)
def get_recall_details(
    recall_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecallStatusResponse:
    _ = current_user
    recall = db.get(Recall, recall_id)
    if recall is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy lệnh thu hồi.",
        )

    assignment_details = []
    for a in recall.assignments:
        assignment_details.append({
            "assignment_id": a.id,
            "batch_id": a.batch_id,
            "organization": a.organization,
            "status": a.status,
            "resolved_by": a.resolved_by,
            "resolved_at": a.resolved_at,
            "notes": a.notes,
        })

    return RecallStatusResponse(
        id=recall.id,
        code=recall.code,
        title=recall.title,
        reason=recall.reason,
        status=recall.status,
        created_at=recall.created_at,
        assignments=assignment_details,
    )
