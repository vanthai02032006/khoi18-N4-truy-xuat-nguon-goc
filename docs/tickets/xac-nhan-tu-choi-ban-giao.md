# Xác nhận chuyển quyền giữ và ghi sự kiện trong cùng giao dịch; từ chối ghi lý do

| Thuộc tính | Giá trị |
| --- | --- |
| Mã ticket | S-35 |
| Loại | Feature (nghiệp vụ + API) |
| Trạng thái | Done |
| Phụ thuộc | **T-35 (SCRUM-51)** |
| Liên quan | T-11 (kiểm tra quyền ở máy chủ), T-25 (nhật ký sự kiện lô) |

## 🎯 Mục tiêu & Mô tả công việc

Hai endpoint xác nhận và từ chối, chỉ bên nhận mới được gọi. Xác nhận đổi tổ chức
đang giữ của lô, cập nhật handovers, ghi sự kiện trong cùng 1 transaction. Từ chối
yêu cầu nhập lý do.

## ✅ Tiêu chí nghiệm thu (DoD / AC)

- [x] Xác nhận đổi chủ sở hữu và ghi đủ 2 sự kiện.
- [x] Từ chối giữ nguyên chủ và lưu vết lý do từ chối; test rollback nếu có lỗi giữa chừng.

## 🛠️ Ràng buộc kỹ thuật & Phụ thuộc

- **Phụ thuộc task:** T-35 (SCRUM-51).
- **Lưu ý kỹ thuật:** Kiểm tra quyền bên nhận chặt chẽ ở máy chủ qua middleware ở T-11.

## 📌 Kết quả triển khai

| Hạng mục | Vị trí |
| --- | --- |
| Hai endpoint | `POST /handovers/{handover_id}/accept`, `POST /handovers/{handover_id}/reject` — [app/routers/handovers.py](../../backend/app/routers/handovers.py) |
| Chốt kiểm quyền bên nhận ở máy chủ | `require_handover_receiver` (không tồn tại → `404`, không phải bên nhận → `403`, phiếu đã xử lý → `400`) |
| Ghi nhật ký trong cùng transaction | `record_event()` **không** tự commit — [app/events.py](../../backend/app/events.py) |
| Lý do từ chối bắt buộc (thiếu → `422`) | `HandoverReject.reason` — [app/schemas.py](../../backend/app/schemas.py) |
| Test theo AC | [tests/test_handover_confirm_reject.py](../../backend/tests/test_handover_confirm_reject.py) |
| Tài liệu API | [backend/README.md](../../backend/README.md) |

**Bằng chứng kiểm chứng**

- `python -m pytest tests -q` → **38 passed**.
- E2E bằng `uvicorn` + `curl`: bên không phải bên nhận → `403`, chưa đăng nhập →
  `401`, phiếu đã xử lý → `400`, phiếu không tồn tại → `404`, thiếu lý do → `422`.
- Xác nhận: `Batch.current_owner` đổi sang bên nhận và nhật ký lô ghi đúng 2 dòng
  (`HANDOVER_ACCEPTED`, `OWNER_CHANGED`) trong cùng một giao dịch.
- Từ chối: `current_owner` giữ nguyên, lý do lưu ở `notes` và trong nội dung sự kiện
  `HANDOVER_REJECTED`.
- Rollback: tiêm lỗi ở sự kiện thứ hai → không đổi chủ, không thêm dòng nhật ký nào.
