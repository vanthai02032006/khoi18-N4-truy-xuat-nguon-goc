"""Router API mời thành viên vào tổ chức, phân quyền & kiểm toán (SCRUM-91)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ROLE_ADMIN, User
from app.schemas import (
    InvitationAcceptRequest,
    InvitationCreateRequest,
    InvitationResponse,
    MemberDeactivateRequest,
    OrganizationAuditLogResponse,
    UserResponse,
)
from app.security import get_current_user, require_admin, require_farmer
from app.services.invitation_service import (
    accept_invitation,
    create_invitation,
    list_organization_audits,
    list_organization_invitations,
    list_organization_members,
    revoke_invitation,
    set_user_active_status,
)

router = APIRouter(
    prefix="/invitations",
    tags=["Organization Invitations & Roles (SCRUM-91)"],
)


@router.post(
    "",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mời nhân viên vào tổ chức qua email",
    description=(
        "Tạo lời mời thành viên với token bảo mật có thời hạn. "
        "Quản trị viên chỉ được mời vào đúng tổ chức của mình; "
        "Admin hệ thống có quyền mời vào bất kỳ tổ chức nào."
    ),
)
def invite_member_endpoint(
    payload: InvitationCreateRequest,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> InvitationResponse:
    base_url = str(request.base_url).rstrip("/")
    invitation, raw_token, invite_link = create_invitation(
        db,
        current_user=current_user,
        email=payload.email,
        role=payload.role,
        target_org_id=payload.organization_id,
        expires_in_hours=payload.expires_in_hours,
        base_url=base_url,
    )
    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        organization_id=invitation.organization_id,
        role=invitation.role,
        expires_at=invitation.expires_at,
        is_used=invitation.is_used,
        is_revoked=invitation.is_revoked,
        invited_by_username=invitation.invited_by_username,
        created_at=invitation.created_at,
        invite_token=raw_token,
        invite_link=invite_link,
    )


@router.get(
    "",
    response_model=list[InvitationResponse],
    status_code=status.HTTP_200_OK,
    summary="Danh sách lời mời của tổ chức",
)
def get_invitations_endpoint(
    limit: int = 50,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[InvitationResponse]:
    invitations = list_organization_invitations(db, current_user=current_user, limit=limit)
    return [
        InvitationResponse(
            id=inv.id,
            email=inv.email,
            organization_id=inv.organization_id,
            role=inv.role,
            expires_at=inv.expires_at,
            is_used=inv.is_used,
            is_revoked=inv.is_revoked,
            invited_by_username=inv.invited_by_username,
            created_at=inv.created_at,
            invite_token=None,  # Không lộ lại token sau khi tạo
            invite_link=None,
        )
        for inv in invitations
    ]


@router.post(
    "/{invitation_id}/revoke",
    response_model=InvitationResponse,
    status_code=status.HTTP_200_OK,
    summary="Thu hồi lời mời tham gia tổ chức",
)
def revoke_invitation_endpoint(
    invitation_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> InvitationResponse:
    invitation = revoke_invitation(db, invitation_id=invitation_id, current_user=current_user)
    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        organization_id=invitation.organization_id,
        role=invitation.role,
        expires_at=invitation.expires_at,
        is_used=invitation.is_used,
        is_revoked=invitation.is_revoked,
        invited_by_username=invitation.invited_by_username,
        created_at=invitation.created_at,
        invite_token=None,
        invite_link=None,
    )


@router.post(
    "/accept",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Chấp nhận lời mời và thiết lập tài khoản mật khẩu",
    description=(
        "Endpoint công khai dành cho nhân viên nhận được lời mời: "
        "xác thực token dùng một lần, tự đặt tên đăng nhập và mật khẩu cá nhân. "
        "Không cấp phép tự nâng vai trò hoặc đổi tổ chức."
    ),
)
def accept_invitation_endpoint(
    payload: InvitationAcceptRequest,
    db: Session = Depends(get_db),
) -> UserResponse:
    new_user = accept_invitation(
        db,
        raw_token=payload.token,
        username=payload.username,
        password=payload.password,
    )
    return UserResponse(
        id=new_user.id,
        username=new_user.username,
        role=new_user.role,
        organization_id=new_user.organization_id,
        email=new_user.email,
    )


@router.get(
    "/members",
    response_model=list[UserResponse],
    status_code=status.HTTP_200_OK,
    summary="Danh sách thành viên thuộc tổ chức",
)
def get_members_endpoint(
    limit: int = 50,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[UserResponse]:
    members = list_organization_members(db, current_user=current_user, limit=limit)
    return [
        UserResponse(
            id=u.id,
            username=u.username,
            role=u.role,
            organization_id=u.organization_id,
            email=u.email,
        )
        for u in members
    ]


@router.post(
    "/members/status",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Vô hiệu hoá hoặc kích hoạt lại tài khoản thành viên",
)
def update_member_status_endpoint(
    payload: MemberDeactivateRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserResponse:
    updated_user = set_user_active_status(
        db,
        user_id=payload.user_id,
        active=payload.active,
        current_user=current_user,
        reason=payload.reason,
    )
    return UserResponse(
        id=updated_user.id,
        username=updated_user.username,
        role=updated_user.role,
        organization_id=updated_user.organization_id,
        email=updated_user.email,
    )


@router.get(
    "/audits",
    response_model=list[OrganizationAuditLogResponse],
    status_code=status.HTTP_200_OK,
    summary="Xem nhật ký kiểm toán tổ chức (chỉ admin)",
)
def get_audits_endpoint(
    limit: int = 50,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[OrganizationAuditLogResponse]:
    return list_organization_audits(db, current_user=current_user, limit=limit)
