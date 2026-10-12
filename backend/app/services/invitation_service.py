"""Service quản lý lời mời thành viên & phân quyền tổ chức (SCRUM-91).

Yêu cầu kỹ thuật & bảo mật:
1. Tích hợp chặt chẽ với mô hình bảng User, role (admin / farmer) và organization_id hiện có.
   Không tạo hệ thống đăng nhập thứ hai.
2. Lời mời sinh token ngẫu nhiên bảo mật (secrets.token_urlsafe(32)), lưu dưới dạng băm SHA-256 (token_hash).
3. Lời mời có thời hạn (expires_at), chỉ dùng một lần (is_used = True) và có thể thu hồi (is_revoked = True).
4. Phân quyền tổ chức nghiêm ngặt (Tenant Isolation):
   - Admin hệ thống (role=admin) có thể mời vào bất kỳ tổ chức nào.
   - Thành viên tổ chức chỉ được mời người vào ĐÚNG tổ chức của mình (current_user.organization_id).
   - Ngăn chặn triệt để hành vi tự đổi organization_id hoặc leo thang đặc quyền (privilege escalation).
5. Vô hiệu hoá thành viên đã nghỉ (is_active = False) và ghi nhật ký kiểm toán (OrganizationAuditLog).
6. Tích hợp gửi email qua biến môi trường (SMTP), không hardcode secret, không gửi mật khẩu qua email.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import (
    ROLE_ADMIN,
    ROLE_FARMER,
    OrganizationAuditLog,
    OrganizationInvitation,
    User,
)
from app.security import hash_password


def hash_token(raw_token: str) -> str:
    """Băm token ngẫu nhiên bằng SHA-256 để lưu trữ an toàn trong CSDL."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


def log_organization_audit(
    db: Session,
    organization_id: str,
    operator_username: str,
    action: str,
    target_email_or_user: str | None = None,
    details: str | None = None,
) -> OrganizationAuditLog:
    """Ghi nhật ký kiểm toán (Audit Trail) cho mọi thao tác nhạy cảm."""
    audit_entry = OrganizationAuditLog(
        organization_id=organization_id,
        operator_username=operator_username,
        action=action,
        target_email_or_user=target_email_or_user,
        details=details,
    )
    db.add(audit_entry)
    db.commit()
    db.refresh(audit_entry)
    return audit_entry


def send_invitation_email(
    to_email: str,
    invite_link: str,
    organization_id: str,
    role: str,
) -> bool:
    """Gửi email mời tham gia tổ chức qua SMTP nếu được cấu hình.

    Đọc thông tin từ biến môi trường: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
    Tuyệt đối không gửi mật khẩu mặc định qua email.
    """
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM", smtp_user or "noreply@agritrace.vn")

    if not smtp_host or not smtp_user or not smtp_password:
        # Nếu chưa cấu hình hạ tầng SMTP bên ngoài, ghi nhận log và cho phép mock
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[AGRI-TRACE] Lời mời tham gia tổ chức {organization_id}"
        msg["From"] = smtp_from
        msg["To"] = to_email

        html_body = f"""
        <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <h2>Lời mời tham gia hệ thống truy xuất nguồn gốc AGRI-TRACE</h2>
            <p>Xin chào,</p>
            <p>Bạn nhận được lời mời tham gia vào tổ chức <strong>{organization_id}</strong> với vai trò <strong>{role}</strong>.</p>
            <p>Vui lòng nhấp vào đường link dưới đây để thiết lập tài khoản và mật khẩu của bạn (đường link có hiệu lực trong thời gian giới hạn):</p>
            <p><a href="{invite_link}" style="display: inline-block; padding: 10px 20px; background-color: #10b981; color: #fff; text-decoration: none; border-radius: 6px;">Kích hoạt tài khoản</a></p>
            <p>Hoặc truy cập trực tiếp: <code>{invite_link}</code></p>
            <p><em>Lưu ý: Vì lý do bảo mật, hệ thống không bao giờ cung cấp sẵn mật khẩu qua email.</em></p>
        </div>
        """
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [to_email], msg.as_string())
        return True
    except Exception:
        return False


def create_invitation(
    db: Session,
    current_user: User,
    email: str,
    role: str = ROLE_FARMER,
    target_org_id: str | None = None,
    expires_in_hours: int = 48,
    base_url: str = "",
) -> tuple[OrganizationInvitation, str, str]:
    """Tạo lời mời thành viên mới vào tổ chức và cấp token bảo mật.

    Kiểm tra phân quyền:
    - Nếu current_user không phải admin: bắt buộc tổ chức phải là current_user.organization_id.
    - Không được phép tự cấp quyền admin nếu người mời không phải admin hệ thống.
    """
    clean_email = email.strip().lower()

    # Kiểm tra xem email đã được dùng cho tài khoản nào chưa
    existing_user_email = db.scalar(select(User).where(User.email == clean_email))
    if existing_user_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Email '{clean_email}' đã được đăng ký cho tài khoản '{existing_user_email.username}'.",
        )

    # Xác định tổ chức mục tiêu
    assigned_org = current_user.organization_id or "ORG_DEFAULT"
    if current_user.role == ROLE_ADMIN and target_org_id:
        assigned_org = target_org_id.strip()
    elif target_org_id and target_org_id.strip() != current_user.organization_id:
        # Người dùng không phải admin toàn quyền cố tình mời sang tổ chức khác
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền mời nhân viên vào tổ chức khác ngoài tổ chức của mình.",
        )

    # Kiểm tra vai trò được gán
    assigned_role = role.strip()
    if assigned_role not in (ROLE_ADMIN, ROLE_FARMER):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Vai trò '{assigned_role}' không hợp lệ (chỉ chấp nhận '{ROLE_ADMIN}' hoặc '{ROLE_FARMER}').",
        )

    if assigned_role == ROLE_ADMIN and current_user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ quản trị viên hệ thống mới có quyền mời thành viên với vai trò quản trị (admin).",
        )

    # Sinh token ngẫu nhiên bảo mật 32 bytes (URL-safe string)
    raw_token = secrets.token_urlsafe(32)
    token_hashed = hash_token(raw_token)
    expires_at = datetime.now() + timedelta(hours=expires_in_hours)

    invitation = OrganizationInvitation(
        email=clean_email,
        organization_id=assigned_org,
        role=assigned_role,
        token_hash=token_hashed,
        expires_at=expires_at,
        is_used=False,
        is_revoked=False,
        invited_by_username=current_user.username,
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)

    invite_link = f"{base_url}/#accept-invite?token={raw_token}"
    send_invitation_email(clean_email, invite_link, assigned_org, assigned_role)

    # Ghi audit log
    log_organization_audit(
        db,
        organization_id=assigned_org,
        operator_username=current_user.username,
        action="INVITE_SENT",
        target_email_or_user=clean_email,
        details=f"Gửi lời mời tham gia tổ chức với vai trò {assigned_role}, hết hạn sau {expires_in_hours}h.",
    )

    return invitation, raw_token, invite_link


def accept_invitation(
    db: Session,
    raw_token: str,
    username: str,
    password: str,
) -> User:
    """Chấp nhận lời mời, kiểm tra token và tạo tài khoản người dùng mới."""
    token_hashed = hash_token(raw_token)

    invitation = db.scalar(
        select(OrganizationInvitation).where(OrganizationInvitation.token_hash == token_hashed)
    )

    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lời mời không tồn tại hoặc token không hợp lệ.",
        )

    if invitation.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lời mời này đã bị thu hồi bởi người quản trị.",
        )

    if invitation.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lời mời này đã được sử dụng trước đó (chỉ dùng một lần).",
        )

    if datetime.now() > invitation.expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lời mời này đã hết thời hạn sử dụng. Vui lòng liên hệ quản trị viên để gửi lại lời mời mới.",
        )

    clean_username = username.strip()
    if len(clean_username) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tên đăng nhập phải có ít nhất 3 ký tự.",
        )

    # Kiểm tra username trùng
    existing_user = db.scalar(select(User).where(User.username == clean_username))
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tên đăng nhập '{clean_username}' đã được sử dụng. Vui lòng chọn tên đăng nhập khác.",
        )

    # Tạo User mới với thông tin ràng buộc từ lời mời (ngăn chặn leo thang quyền)
    new_user = User(
        username=clean_username,
        password=hash_password(password),
        role=invitation.role,
        organization_id=invitation.organization_id,
        email=invitation.email,
        is_active=True,
    )
    db.add(new_user)

    # Đánh dấu lời mời đã sử dụng
    invitation.is_used = True
    db.commit()
    db.refresh(new_user)

    # Ghi audit log
    log_organization_audit(
        db,
        organization_id=invitation.organization_id,
        operator_username=clean_username,
        action="INVITE_ACCEPTED",
        target_email_or_user=invitation.email,
        details=f"Người dùng '{clean_username}' đã kích hoạt tài khoản thành công với vai trò {invitation.role}.",
    )

    return new_user


def revoke_invitation(
    db: Session,
    invitation_id: int,
    current_user: User,
) -> OrganizationInvitation:
    """Thu hồi một lời mời đang chờ."""
    invitation = db.get(OrganizationInvitation, invitation_id)
    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lời mời ID={invitation_id}.",
        )

    # Kiểm tra quyền: admin hệ thống hoặc người cùng tổ chức
    if current_user.role != ROLE_ADMIN and invitation.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền thu hồi lời mời của tổ chức khác.",
        )

    if invitation.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lời mời đã được sử dụng, không thể thu hồi.",
        )

    invitation.is_revoked = True
    db.commit()
    db.refresh(invitation)

    log_organization_audit(
        db,
        organization_id=invitation.organization_id,
        operator_username=current_user.username,
        action="INVITE_REVOKED",
        target_email_or_user=invitation.email,
        details=f"Đã thu hồi lời mời ID={invitation_id} gửi cho email {invitation.email}.",
    )

    return invitation


def set_user_active_status(
    db: Session,
    user_id: int,
    active: bool,
    current_user: User,
    reason: str | None = None,
) -> User:
    """Vô hiệu hoá hoặc kích hoạt lại tài khoản thành viên trong tổ chức."""
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy người dùng ID={user_id}.",
        )

    if target_user.id == current_user.id and not active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể tự vô hiệu hoá chính tài khoản của bạn.",
        )

    # Kiểm tra quyền cách ly tổ chức
    if current_user.role != ROLE_ADMIN and target_user.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền thay đổi trạng thái tài khoản của tổ chức khác.",
        )

    target_user.is_active = active
    db.commit()
    db.refresh(target_user)

    action_name = "MEMBER_ACTIVATED" if active else "MEMBER_DEACTIVATED"
    log_organization_audit(
        db,
        organization_id=target_user.organization_id or "ORG_UNKNOWN",
        operator_username=current_user.username,
        action=action_name,
        target_email_or_user=target_user.username,
        details=f"Thay đổi trạng thái tài khoản thành is_active={active}. Lý do: {reason or 'Không có'}",
    )

    return target_user


def list_organization_invitations(
    db: Session,
    current_user: User,
    limit: int = 50,
) -> list[OrganizationInvitation]:
    """Lấy danh sách lời mời của tổ chức hiện tại (hoặc tất cả nếu là admin)."""
    query = select(OrganizationInvitation).order_by(desc(OrganizationInvitation.created_at))
    if current_user.role != ROLE_ADMIN:
        query = query.where(OrganizationInvitation.organization_id == current_user.organization_id)
    return list(db.scalars(query.limit(limit)).all())


def list_organization_members(
    db: Session,
    current_user: User,
    limit: int = 50,
) -> list[User]:
    """Lấy danh sách thành viên thuộc tổ chức hiện tại."""
    query = select(User).order_by(User.id)
    if current_user.role != ROLE_ADMIN:
        query = query.where(User.organization_id == current_user.organization_id)
    return list(db.scalars(query.limit(limit)).all())


def list_organization_audits(
    db: Session,
    current_user: User,
    limit: int = 50,
) -> list[OrganizationAuditLog]:
    """Lấy danh sách nhật ký kiểm toán tổ chức."""
    query = select(OrganizationAuditLog).order_by(desc(OrganizationAuditLog.created_at))
    if current_user.role != ROLE_ADMIN:
        query = query.where(OrganizationAuditLog.organization_id == current_user.organization_id)
    return list(db.scalars(query.limit(limit)).all())
