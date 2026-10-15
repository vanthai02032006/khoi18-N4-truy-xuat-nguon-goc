"""Test luồng xác nhận / từ chối bàn giao lô nông sản.

Bám theo tiêu chí nghiệm thu của task:

- **Xác nhận**: đổi chủ sở hữu lô (``Batch.current_owner`` → bên nhận) **và ghi đủ
  2 sự kiện** (``HANDOVER_ACCEPTED`` + ``OWNER_CHANGED``).
- **Từ chối**: **giữ nguyên chủ** và **lưu vết lý do** (ở ``notes`` và trong
  nội dung sự kiện); thiếu lý do → ``422``.
- **Chỉ bên nhận** được gọi (403 với mọi tài khoản khác, kể cả ``admin``).
- **Rollback**: lỗi giữa chừng thì *không* thay đổi nào được ghi xuống CSDL.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.events import MAX_ACTOR_LENGTH, record_event
from app.models import (
    EVENT_TYPE_HANDOVER_ACCEPTED,
    EVENT_TYPE_HANDOVER_PENDING,
    EVENT_TYPE_HANDOVER_REJECTED,
    EVENT_TYPE_OWNER_CHANGED,
    HANDOVER_STATUS_ACCEPTED,
    HANDOVER_STATUS_PENDING,
    HANDOVER_STATUS_REJECTED,
    Batch,
    BatchEvent,
    Handover,
    User,
)
from app.routers.handovers import (
    accept_handover,
    create_handover,
    reject_handover,
    require_handover_receiver,
)
from app.schemas import HandoverAccept, HandoverCreate, HandoverReject

RECEIVER_NAME = "Công ty Thu mua Xuất khẩu Mekong"
SENDER_NAME = "Hợp tác xã Xoài Mỹ Xương"


# ------------------------------------------------------------------ Tiện ích ---
def _create_pending_handover(
    session: Session,
    batch: Batch,
    sender: User,
    receiver: User,
) -> Handover:
    """Tạo một phiếu bàn giao đang chờ xử lý bằng chính endpoint ``POST /handovers``."""
    create_handover(
        payload=HandoverCreate(
            batch_id=batch.id,
            receiver_id=receiver.id,
            receiver_name=RECEIVER_NAME,
            notes="Bàn giao lô xoài sang kho đóng gói.",
        ),
        current_user=sender,
        db=session,
    )
    return session.scalars(select(Handover).order_by(Handover.id.desc())).first()


def _events(session: Session, batch_id: int) -> list[BatchEvent]:
    """Toàn bộ sự kiện của lô, sắp theo thứ tự ghi."""
    return list(
        session.scalars(
            select(BatchEvent)
            .where(BatchEvent.batch_id == batch_id)
            .order_by(BatchEvent.id.asc())
        ).all()
    )


def _event_types(session: Session, batch_id: int) -> list[str]:
    return [event.event_type for event in _events(session, batch_id)]


# ------------------------------------------------------- Tạo phiếu bàn giao ---
def test_tao_phieu_giu_nguyen_chu_va_ghi_su_kien_pending(session, batch, sender, receiver) -> None:
    """Tạo phiếu chỉ ghi nhận chờ xử lý - lô **vẫn thuộc bên giao**."""
    handover = _create_pending_handover(session, batch, sender, receiver)

    assert handover.status == HANDOVER_STATUS_PENDING
    assert handover.receiver_id == receiver.id
    session.refresh(batch)
    assert batch.current_owner == SENDER_NAME  # chưa đổi chủ
    assert _event_types(session, batch.id) == [EVENT_TYPE_HANDOVER_PENDING]


def test_khong_tao_duoc_phieu_thu_hai_khi_dang_cho(session, batch, sender, receiver) -> None:
    """Mỗi lô chỉ có tối đa 1 phiếu chờ xử lý (chặn ở tầng ứng dụng)."""
    _create_pending_handover(session, batch, sender, receiver)

    with pytest.raises(HTTPException) as exc_info:
        create_handover(
            payload=HandoverCreate(
                batch_id=batch.id,
                receiver_id=receiver.id,
                receiver_name="Bên nhận khác",
            ),
            current_user=sender,
            db=session,
        )
    assert exc_info.value.status_code == 400


# ------------------------------------------------- Quyền: chỉ bên nhận gọi ---
def test_chi_ben_nhan_duoc_xac_nhan(session, batch, sender, receiver, stranger) -> None:
    """Bên nhận gọi được; bên giao và ``admin`` đều nhận **403**."""
    handover = _create_pending_handover(session, batch, sender, receiver)

    # Bên giao (người tạo phiếu) không được xác nhận.
    with pytest.raises(HTTPException) as by_sender:
        require_handover_receiver(handover_id=handover.id, current_user=sender, db=session)
    assert by_sender.value.status_code == 403

    # Tài khoản admin nhưng không phải bên nhận cũng không được (card: chỉ bên nhận).
    with pytest.raises(HTTPException) as by_stranger:
        require_handover_receiver(handover_id=handover.id, current_user=stranger, db=session)
    assert by_stranger.value.status_code == 403

    # Bên nhận đi qua bình thường.
    assert require_handover_receiver(
        handover_id=handover.id, current_user=receiver, db=session
    ).id == handover.id


def test_hai_endpoint_deu_bat_buoc_kiem_tra_ben_nhan() -> None:
    """Chốt chặn wiring: cả accept và reject phải dùng dependency kiểm quyền.

    Nếu ai đó gỡ dependency khỏi chữ ký endpoint (mở lại lỗ hổng cho mọi tài
    khoản), test này đỏ ngay.
    """
    import inspect

    for endpoint in (accept_handover, reject_handover):
        parameter = inspect.signature(endpoint).parameters["handover"]
        assert parameter.default.dependency is require_handover_receiver


def test_phieu_khong_gan_tai_khoan_ben_nhan_tra_403(session, batch, sender, receiver) -> None:
    """Phiếu không gắn tài khoản bên nhận thì không ai xác nhận/từ chối được."""
    create_handover(
        payload=HandoverCreate(
            batch_id=batch.id,
            receiver_id=None,  # bên nhận chưa có tài khoản hệ thống
            receiver_name="Đối tác chưa có tài khoản",
        ),
        current_user=sender,
        db=session,
    )
    handover = session.scalars(select(Handover)).first()

    with pytest.raises(HTTPException) as exc_info:
        require_handover_receiver(handover_id=handover.id, current_user=receiver, db=session)
    assert exc_info.value.status_code == 403


def test_phieu_da_xu_ly_tra_400(session, batch, sender, receiver) -> None:
    """Phiếu không còn ``pending`` -> 400 (sau khi đã xác nhận)."""
    handover = _create_pending_handover(session, batch, sender, receiver)
    accept_handover(payload=None, handover=handover, current_user=receiver, db=session)

    with pytest.raises(HTTPException) as exc_info:
        require_handover_receiver(handover_id=handover.id, current_user=receiver, db=session)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------- XÁC NHẬN (accept) ---
def test_xac_nhan_doi_chu_so_huu_va_ghi_du_hai_su_kien(session, batch, sender, receiver) -> None:
    """AC: xác nhận đổi chủ sở hữu **và** ghi đủ 2 sự kiện."""
    handover = _create_pending_handover(session, batch, sender, receiver)
    events_before = len(_events(session, batch.id))

    result = accept_handover(
        payload=HandoverAccept(notes="Đã kiểm tra chất lượng."),
        handover=handover,
        current_user=receiver,
        db=session,
    )

    # 1. Chủ sở hữu lô đổi sang bên nhận.
    session.refresh(batch)
    assert batch.current_owner == RECEIVER_NAME
    assert result.current_batch_owner == RECEIVER_NAME

    # 2. Trạng thái phiếu + thời điểm xử lý.
    session.refresh(handover)
    assert handover.status == HANDOVER_STATUS_ACCEPTED
    assert handover.updated_at is not None
    assert result.status == HANDOVER_STATUS_ACCEPTED

    # 3. Đủ 2 sự kiện mới, đúng loại và đúng thứ tự.
    new_events = _events(session, batch.id)[events_before:]
    assert [event.event_type for event in new_events] == [
        EVENT_TYPE_HANDOVER_ACCEPTED,
        EVENT_TYPE_OWNER_CHANGED,
    ]
    assert result.recorded_events == [
        EVENT_TYPE_HANDOVER_ACCEPTED,
        EVENT_TYPE_OWNER_CHANGED,
    ]

    # 4. Sự kiện đổi chủ ghi rõ chuyển từ bên giao sang bên nhận, kèm chuỗi băm.
    owner_event = new_events[1]
    assert SENDER_NAME in owner_event.payload
    assert RECEIVER_NAME in owner_event.payload
    assert owner_event.actor == receiver.username
    # Sự kiện thứ hai phải nối tiếp hash của sự kiện thứ nhất (chuỗi băm liền mạch).
    assert owner_event.previous_hash == new_events[0].hash
    assert len(owner_event.hash) == 64


def test_xac_nhan_bang_tai_khoan_khac_bi_chan_khong_doi_gi(
    session, batch, sender, receiver, stranger
) -> None:
    """AC: gọi trái phép bị chặn và **không** làm thay đổi dữ liệu."""
    handover = _create_pending_handover(session, batch, sender, receiver)

    with pytest.raises(HTTPException) as exc_info:
        require_handover_receiver(handover_id=handover.id, current_user=stranger, db=session)
    assert exc_info.value.status_code == 403

    session.refresh(batch)
    session.refresh(handover)
    assert batch.current_owner == SENDER_NAME
    assert handover.status == HANDOVER_STATUS_PENDING


# ----------------------------------------------------- TỪ CHỐI (reject) ---
def test_tu_choi_bat_buoc_nhap_ly_do() -> None:
    """AC: từ chối phải nhập lý do - thiếu/rỗng/toàn khoảng trắng đều bị chặn."""
    with pytest.raises(ValidationError):
        HandoverReject(reason="")
    with pytest.raises(ValidationError):
        HandoverReject(reason="    ")
    with pytest.raises(ValidationError):
        HandoverReject()


def test_tu_choi_giu_nguyen_chu_va_luu_vet_ly_do(session, batch, sender, receiver) -> None:
    """AC: từ chối giữ nguyên chủ và lưu vết lý do từ chối."""
    handover = _create_pending_handover(session, batch, sender, receiver)
    reason = "Lô bị dập trong quá trình vận chuyển, không đạt chuẩn."

    result = reject_handover(
        payload=HandoverReject(reason=reason),
        handover=handover,
        current_user=receiver,
        db=session,
    )

    # 1. Chủ sở hữu lô **giữ nguyên** ở bên giao.
    session.refresh(batch)
    assert batch.current_owner == SENDER_NAME
    assert result.current_batch_owner == SENDER_NAME

    # 2. Phiếu chuyển rejected và lý do được lưu ở notes.
    session.refresh(handover)
    assert handover.status == HANDOVER_STATUS_REJECTED
    assert handover.updated_at is not None
    assert reason in handover.notes

    # 3. Sự kiện từ chối có kèm lý do để tra cứu nguồn gốc.
    rejected_events = [
        event
        for event in _events(session, batch.id)
        if event.event_type == EVENT_TYPE_HANDOVER_REJECTED
    ]
    assert len(rejected_events) == 1
    assert reason in rejected_events[0].payload
    assert result.recorded_events == [EVENT_TYPE_HANDOVER_REJECTED]


# --------------------------------------------------------- ROLLBACK ---
def _break_second_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """Giả lập lỗi CSDL ở sự kiện **thứ hai** để thử rollback giữa chừng.

    Đây là cách mô phỏng lỗi có kiểm soát: sự kiện thứ nhất đã ``db.add()``,
    quyền giữ lô cũng đã đổi trong session - nếu router không gom đúng một
    transaction thì dữ liệu sẽ bị ghi dở dang.
    """
    from app.routers import handovers as handovers_router

    original = handovers_router.record_event
    calls = {"count": 0}

    def flaky_record_event(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("Lỗi CSDL giả lập ở sự kiện thứ hai")
        return original(*args, **kwargs)

    monkeypatch.setattr(handovers_router, "record_event", flaky_record_event)


def test_rollback_khi_loi_giua_chung_o_xac_nhan(
    session, batch, sender, receiver, monkeypatch
) -> None:
    """AC: lỗi giữa chừng -> **không** đổi chủ, **không** thêm sự kiện nào."""
    handover = _create_pending_handover(session, batch, sender, receiver)
    events_before = len(_events(session, batch.id))
    _break_second_event(monkeypatch)

    with pytest.raises(RuntimeError):
        accept_handover(
            payload=None,
            handover=handover,
            current_user=receiver,
            db=session,
        )

    # Router chỉ rollback trong nhánh lỗi CSDL, nên ở đây test tự dọn session
    # giống như `get_db` đóng session khi request kết thúc.
    session.rollback()

    session.refresh(batch)
    session.refresh(handover)
    assert batch.current_owner == SENDER_NAME
    assert handover.status == HANDOVER_STATUS_PENDING
    assert len(_events(session, batch.id)) == events_before


def test_rollback_khi_loi_giua_chung_o_tu_choi(
    session, batch, sender, receiver, monkeypatch
) -> None:
    """AC: lỗi khi ghi sự kiện từ chối -> phiếu vẫn ``pending``, không lưu lý do."""
    handover = _create_pending_handover(session, batch, sender, receiver)

    from app.routers import handovers as handovers_router

    def failing_record_event(*args, **kwargs):
        raise RuntimeError("Lỗi CSDL giả lập khi ghi sự kiện từ chối")

    monkeypatch.setattr(handovers_router, "record_event", failing_record_event)

    with pytest.raises(RuntimeError):
        reject_handover(
            payload=HandoverReject(reason="Không đạt chuẩn."),
            handover=handover,
            current_user=receiver,
            db=session,
        )

    session.rollback()

    session.refresh(handover)
    session.refresh(batch)
    assert handover.status == HANDOVER_STATUS_PENDING
    assert handover.notes == "Bàn giao lô xoài sang kho đóng gói."  # không dính lý do
    assert batch.current_owner == SENDER_NAME
    assert EVENT_TYPE_HANDOVER_REJECTED not in _event_types(session, batch.id)


def test_rollback_khong_de_lai_su_kien_mo_coi(session, batch, sender, receiver, monkeypatch) -> None:
    """Sau rollback, nhật ký của lô không tăng thêm dòng nào."""
    handover = _create_pending_handover(session, batch, sender, receiver)
    count_before = session.scalar(
        select(func.count()).select_from(BatchEvent).where(BatchEvent.batch_id == batch.id)
    )
    _break_second_event(monkeypatch)

    with pytest.raises(RuntimeError):
        accept_handover(payload=None, handover=handover, current_user=receiver, db=session)

    session.rollback()
    count_after = session.scalar(
        select(func.count()).select_from(BatchEvent).where(BatchEvent.batch_id == batch.id)
    )
    assert count_after == count_before


# --------------------------------------------------- Ràng buộc dữ liệu ---
def test_mot_lo_chi_co_mot_phieu_cho_o_tang_csdl(session, batch, sender, receiver) -> None:
    """Chỉ mục duy nhất một phần chặn phiếu ``pending`` thứ hai ở tầng CSDL."""
    from sqlalchemy.exc import IntegrityError

    _create_pending_handover(session, batch, sender, receiver)

    session.add(
        Handover(
            batch_id=batch.id,
            sender_id=sender.id,
            sender_name=SENDER_NAME,
            receiver_id=receiver.id,
            receiver_name="Bên nhận khác",
            status=HANDOVER_STATUS_PENDING,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_ghi_su_kien_ten_tai_khoan_qua_dai(session, batch) -> None:
    """``record_event`` chặn sớm tài khoản > 100 ký tự (giới hạn cột ``actor``)."""
    with pytest.raises(ValueError):
        record_event(
            session,
            batch_id=batch.id,
            event_type="BATCH_CREATED",
            actor="x" * (MAX_ACTOR_LENGTH + 1),
        )


def test_nhat_ky_sap_theo_thoi_gian(session, batch, sender, receiver) -> None:
    """Nhật ký lô trả về theo đúng thứ tự thời gian đã ghi (timestamp ISO)."""
    handover = _create_pending_handover(session, batch, sender, receiver)
    accept_handover(payload=None, handover=handover, current_user=receiver, db=session)

    timestamps = [event.timestamp for event in _events(session, batch.id)]
    assert timestamps == sorted(timestamps)
    assert all(isinstance(ts, str) for ts in timestamps)
    assert all("T" in ts for ts in timestamps)  # ISO-8601 do datetime.isoformat()


def test_hoan_tat_luong_ban_giao_lien_tiep(session, batch, sender, receiver) -> None:
    """Sau khi accepted, lô lại tạo được phiếu mới (chỉ mục unique chỉ chặn pending)."""
    handover = _create_pending_handover(session, batch, sender, receiver)
    accept_handover(payload=None, handover=handover, current_user=receiver, db=session)

    create_handover(
        payload=HandoverCreate(
            batch_id=batch.id,
            receiver_id=sender.id,
            receiver_name="Đơn vị vận chuyển",
        ),
        current_user=receiver,
        db=session,
    )
    statuses = list(
        session.scalars(
            select(Handover.status).where(Handover.batch_id == batch.id).order_by(Handover.id)
        ).all()
    )
    assert statuses == [HANDOVER_STATUS_ACCEPTED, HANDOVER_STATUS_PENDING]


def test_lich_su_ban_giao_loc_theo_lo_va_trang_thai(session, batch, sender, receiver) -> None:
    """Danh sách bàn giao lọc được theo lô và trạng thái, kèm chủ sở hữu hiện tại."""
    from app.routers.handovers import list_handovers

    handover = _create_pending_handover(session, batch, sender, receiver)
    accept_handover(payload=None, handover=handover, current_user=receiver, db=session)

    all_rows = list_handovers(batch_id=batch.id, status_filter=None, db=session)
    assert [row.status for row in all_rows] == [HANDOVER_STATUS_ACCEPTED]
    assert all_rows[0].current_batch_owner == RECEIVER_NAME

    pending_rows = list_handovers(batch_id=batch.id, status_filter="pending", db=session)
    assert pending_rows == []


def test_danh_sach_rong_khi_chua_co_ban_giao(session, batch) -> None:
    """Chưa có phiếu nào -> danh sách rỗng (không lỗi)."""
    from app.routers.handovers import list_handovers

    assert list_handovers(batch_id=batch.id, status_filter=None, db=session) == []


def test_thoi_diem_xu_ly_khong_o_tuong_lai(session, batch, sender, receiver) -> None:
    """``updated_at`` được đặt tự động khi xử lý (không cần router set tay)."""
    handover = _create_pending_handover(session, batch, sender, receiver)
    assert handover.updated_at is None

    accept_handover(payload=None, handover=handover, current_user=receiver, db=session)
    session.refresh(handover)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert handover.updated_at is not None
    assert abs((handover.updated_at - now_utc).total_seconds()) < 300


def test_ban_giao_khong_ton_tai_tra_404_khi_xac_nhan(session, receiver) -> None:
    """``require_handover_receiver`` trả 404 trước khi chạm tới logic nghiệp vụ."""
    with pytest.raises(HTTPException) as exc_info:
        require_handover_receiver(handover_id=9999, current_user=receiver, db=session)
    assert exc_info.value.status_code == 404


def test_lo_khong_ton_tai_khi_tao_phieu(session, sender, receiver) -> None:
    """Tạo phiếu cho lô không tồn tại -> 404."""
    with pytest.raises(HTTPException) as exc_info:
        create_handover(
            payload=HandoverCreate(
                batch_id=9999,
                receiver_id=receiver.id,
                receiver_name=RECEIVER_NAME,
            ),
            current_user=sender,
            db=session,
        )
    assert exc_info.value.status_code == 404


def test_lo_chua_co_chu_thi_lay_theo_chu_vung_trong(session, sender, receiver) -> None:
    """Lô chưa ghi chủ sở hữu -> lấy theo chủ vùng trồng khi tạo phiếu đầu tiên."""
    from app.models import Farm

    farm = Farm(name="Vùng mới", location="Đồng Tháp", area=1.0, owner="Chủ vùng trồng")
    session.add(farm)
    session.flush()
    batch = Batch(
        farm_id=farm.id,
        product_name="Nhãn xuồng",
        quantity=10.0,
        harvest_date=date(2026, 9, 25),
    )
    session.add(batch)
    session.commit()

    create_handover(
        payload=HandoverCreate(
            batch_id=batch.id,
            receiver_id=receiver.id,
            receiver_name=RECEIVER_NAME,
        ),
        current_user=sender,
        db=session,
    )
    session.refresh(batch)
    assert batch.current_owner == "Chủ vùng trồng"


def test_batch_owner_giu_nguyen_khi_phieu_cho(session, batch, sender, receiver) -> None:
    """Trong lúc chờ xử lý, chủ sở hữu lô không đổi dù gọi lại các API đọc."""
    handover = _create_pending_handover(session, batch, sender, receiver)
    from app.routers.handovers import get_handover

    detail = get_handover(handover_id=handover.id, db=session)
    assert detail.current_batch_owner == SENDER_NAME
    session.refresh(batch)
    assert batch.current_owner == SENDER_NAME


def test_tai_khoan_thuc_hien_duoc_ghi_trong_su_kien(
    session, batch, sender, receiver
) -> None:
    """``actor`` của sự kiện là **tài khoản** thực hiện, không phải tên tổ chức."""
    handover = _create_pending_handover(session, batch, sender, receiver)
    accept_handover(payload=None, handover=handover, current_user=receiver, db=session)

    new_events = [
        event
        for event in _events(session, batch.id)
        if event.event_type in (EVENT_TYPE_HANDOVER_ACCEPTED, EVENT_TYPE_OWNER_CHANGED)
    ]
    assert [event.actor for event in new_events] == [receiver.username, receiver.username]


def test_receiver_fixture_la_tai_khoan_rieng_biet(session, sender, receiver) -> None:
    """Bảo đảm fixture bên giao/bên nhận là 2 tài khoản khác nhau (test không vô nghĩa)."""
    assert sender.id != receiver.id
    assert session.get(User, receiver.id).username == "cty_mekong"
