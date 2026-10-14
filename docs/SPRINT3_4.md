# Sprint 3–4: chức năng trên web và mã Jira

Nhánh `integrate/sprint-3-4`. Bộ test: **192 test xanh** (140 ở `tests/`, 52 ở `backend/tests/`).

## Sprint 3: Tách, gộp và truy ngược nguồn gốc

Sprint Goal: *Lô tách và gộp được ở hợp tác xã, và từ lô bất kỳ truy ngược ra đúng vùng trồng gốc.*

| Story | Jira story | Task trên Jira | Ở đâu trên web | API |
|---|---|---|---|---|
| S-17 Tách một lô thành nhiều lô con | SCRUM-101 | SCRUM-55, 56, 57 | **Lô hàng** › nút **Tách** | `POST /batches/{id}/split` |
| S-18 Tổng lô con không vượt lô mẹ, kể cả khi tách đồng thời | SCRUM-102 | SCRUM-58, 59 | Form Tách: hiện số dư và chặn khi nhập vượt | (trong `split`) |
| S-19 Gộp nhiều lô cùng sản phẩm | SCRUM-103 | SCRUM-60, 61, 62 | **Lô hàng** › **Bật chế độ gộp lô** | `POST /batches/merge` |
| S-20 Sự kiện tách/gộp ghi vào chuỗi của mọi lô liên quan | SCRUM-104 | SCRUM-63, 64 | **Lô hàng** › nút **Lịch sử** (dòng thời gian + kiểm toàn vẹn) | `GET /batches/{id}/events` |
| S-21 Truy ngược tới lô thu hoạch gốc kèm vùng trồng | SCRUM-105 | SCRUM-65, 66, 67 | **Phả hệ** › **Nguồn gốc** | `GET /batches/{id}/trace-back` |
| S-22 Bộ dữ liệu mẫu phả hệ có đáp án đếm tay | SCRUM-106 | SCRUM-68, 69 | (không có màn hình) `backend/scripts/seed_lineage_graph.py`, `backend/data/lineage_benchmark.json` | — |
| S-23 Xem lịch sử tổ tiên thuộc tổ chức khác | SCRUM-89 | SCRUM-70, 71 | **Phả hệ** › Nguồn gốc / Dòng thời gian của lô nhận từ tổ chức khác | quy tắc `viewable_batch_ids` |
| S-24 Bàn giao chờ quá 48 giờ bị đánh dấu | SCRUM-85 | SCRUM-72, 73 | **Bàn giao**: nhãn **⚠ Quá hạn — chờ nhận** | job mỗi giờ + `POST /handovers/scan-overdue` |
| S-25 Trang chi tiết lô: lô mẹ và lô con trực tiếp | SCRUM-97 | SCRUM-74, 75 | **Lô hàng** › nút **Xem** → **Phả hệ** › **Tổng quan** | `GET /batches/{id}/direct-summary` |
| S-14 (dồn từ Sprint 2) Danh sách lô đang giữ, 20 lô/trang | — | SCRUM-49 | **Lô hàng**: mới nhất trước, nút **Xem thêm 20 lô cũ hơn** | `GET /batches?limit=20&cursor=…&holding=true` |

## Sprint 4: Truy xuôi và nơi lô đang nằm

Sprint Goal: *Từ một lô gốc chỉ ra được mọi lô con đã phát sinh và nơi chúng đang nằm.*

| Story | Jira | Ở đâu trên web | API |
|---|---|---|---|
| S-26 Truy xuôi mọi hậu duệ qua nhiều tầng tách/gộp | SCRUM-107 | **Phả hệ** › **Hậu duệ & nơi giữ** | `GET /batches/{id}/trace-forward` |
| S-27 Mỗi hậu duệ kèm tổ chức, địa điểm, khối lượng còn | SCRUM-108 | cùng màn hình: cột Tổ chức, Địa điểm, Còn lại, Trạng thái + tổng còn trên kệ | (trong `trace-forward`) |
| S-28 Sơ đồ phả hệ dạng cây, thu gọn được | SCRUM-109 | **Phả hệ** › **Sơ đồ phả hệ** (tổ tiên trái, lô đang xem giữa, hậu duệ phải; nhánh sâu hơn 2 tầng thu gọn, bấm ô ＋ để mở) | `GET /batches/{id}/lineage-graph` |
| S-29 Ghi nhận lô đã bán hết | SCRUM-110 | **Lô hàng** › nút **Đã bán hết**; sau đó chặn tách, gộp, bàn giao | `POST /batches/{id}/consume` |
| S-30 Chia lô về từng cửa hàng | SCRUM-98 | Tab **Địa điểm** (khai báo cửa hàng/kho), **Lô hàng** › nút **Vị trí**, form Tách chọn địa điểm cho từng lô con | `POST/GET /locations`, `POST /batches/{id}/location` |
| S-31 Nhật ký thao tác | SCRUM-90 | Tab **Nhật ký** (chỉ admin): lọc theo người, hành động, thao tác bị từ chối | `GET /audit-logs` |
| S-32 Nhập sản phẩm và thửa từ CSV | SCRUM-99 | **Thửa đất** và **Danh mục** › khối **Nhập hàng loạt từ CSV** (chỉ admin) | `POST /imports/products`, `POST /imports/farms` |

## Lỗi đã sửa trong lúc tích hợp

1. Gọi API không cần đăng nhập vẫn đọc được lô và phiếu bàn giao (`/batches`, `/batches/{id}`, `/handovers`): nay bắt buộc đăng nhập (S-05).
2. Nút Gộp lô báo lỗi 500, vì giao diện gửi `items` còn API đọc `parents`.
3. Tách lô không ghi `batch_relations`, nên truy ngược/xuôi và quyền xem tổ tiên bỏ sót các lô tách.
4. Truy xuôi gặp chu trình thì trả danh sách rỗng (thu hồi sẽ sót lô, rủi ro R-03): nay báo lỗi 409.
5. `GET /batches/{id}` trả 500 với mọi lô đã từng tách (schema `children` lệch kiểu).
6. Nông hộ tạo lô xong không tách được chính lô của mình (kiểm quyền so header với tên tổ chức).
7. Tạo lô không ghi sự kiện thu hoạch (AC S-10).
8. Dòng thời gian lọc sự kiện theo tổ chức trong header: chuỗi bị cắt và báo đứt mạch giả. Nay kiểm quyền theo lô.
9. Tổ chức của request trước "dính" sang request sau khi không gửi header.
10. Tìm theo mã `VT0003` ra thêm lô có ID 3.
11. CI chỉ chạy `backend/tests`, bỏ qua toàn bộ thư mục `tests/`.

## Ghi công

- Truy ngược, truy xuôi, sơ đồ, chi tiết lô: **Phan Quang** (`feature/phanquang-tasks`). Chuyển vào đúng vị trí, viết lại phần lõi thành `services/lineage_service.py`, giữ nguyên API và schema.
- Job bàn giao quá hạn: ý tưởng của **Lê Hoàng Quân** (`feature/quan-5-tasks`). Viết lại để chỉ gắn nhãn, không đổi trạng thái (đúng AC).
- Tách/gộp và test nghiệm thu: `feature/S-17-S-18-S-19-split-and-merge-batches`.
- Địa điểm, bán hết, sự cố: `feature/S-19-danh-dau-su-co-lenh-thu-hoi`.
- Nền tích hợp, CI, sao lưu, toàn vẹn: `integration/team-sync-clean`.

## Còn tồn

- **Hai cách xác định tổ chức song song:** `organization_id` (mã) và `current_holder_org` (tên). Các quy tắc quyền đã thống nhất qua `user_org_keys` / `user_holds_batch`, nhưng nên gộp hẳn về một cột (việc làm lại của Sprint 1).
- **S-23 AC3 chưa có:** lô đã bàn giao đi thì bên giao vẫn thấy cả các bước sau.
- **S-22:** đồ thị do `seed_lineage_graph.py` dựng (lô `LOT-GRAPH-*`) khác đồ thị trong tệp đáp án (lô `B-*`). Cần hai người đếm tay đáp án cho dữ liệu seed.
- **CSDL vẫn là SQLite:** `SELECT … FOR UPDATE` không có tác dụng, nên S-18 (hai người tách cùng lúc) chỉ được đảm bảo thật khi chuyển sang PostgreSQL.
- **Chưa gộp nhánh `feature/S42`** (chuỗi lạnh, Sprint 6): xung đột 6 chỗ với phần chuỗi lạnh đã có.
- **Giao diện điện thoại cuộn ngang** ngay từ trang đăng nhập (thanh điều hướng, tiêu đề lớn). Lỗi có từ trước, không thuộc Sprint 3–4.
