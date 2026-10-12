# TTCS-K18C4-N4
Dự án TTCS K18C4 - Truy xuất nguồn gốc và giám sát chuỗi lạnh nông sản

## Cấu trúc thư mục

| Thư mục | Nội dung |
| --- | --- |
| `backend/` | FastAPI + SQLite + SQLAlchemy (chi tiết xem `backend/README.md`) |
| `frontend/` | Demo giao diện: `index.html`, `css/style.css`, `js/app.js` — HTML5 + CSS + JavaScript thuần, không framework |
| `docs/` | Tài liệu dự án |

## Chạy backend

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

- API: <http://127.0.0.1:8000>
- Swagger UI: <http://127.0.0.1:8000/docs>

## Chạy frontend (demo)

```powershell
cd frontend
python -m http.server 5500
```

Mở <http://127.0.0.1:5500>. Có thể mở trực tiếp `frontend/index.html`,
nhưng nên chạy qua static server để `fetch`/CORS hoạt động ổn định nhất.

Frontend gọi API tại `http://127.0.0.1:8000` (hằng số `API_BASE_URL` trong `frontend/js/app.js`).

### Đăng nhập (Sprint 4)

Mở trang → hiện **màn hình đăng nhập** (`POST /auth/login`). Tài khoản demo do
backend tạo tự động ở lần chạy đầu tiên:

| Tài khoản | Mật khẩu | Thấy được trên giao diện |
| --- | --- | --- |
| `admin` | `123456` | Toàn bộ: thẻ vùng trồng, lô nông sản **và thẻ "Tài khoản hệ thống" (`GET /users`)** |
| `farmer` | `123456` | Vùng trồng + lô nông sản (thẻ "Tài khoản hệ thống" **bị ẩn**) |

- **Không dùng JWT:** sau khi đăng nhập, frontend lưu `username`/`password` vào
  `sessionStorage` (khoá `ttcs.session`) và gửi kèm header **HTTP Basic** ở mỗi
  request cần quyền.
- Đóng tab là mất phiên (sessionStorage); bấm nút **Đăng xuất** để xoá phiên ngay.
- Backend trả **`401`** khi thiếu/sai thông tin đăng nhập và **`403`** khi đã đăng
  nhập nhưng sai vai trò (chi tiết xem `backend/README.md`, mục Sprint 4).

### CRUD & phân quyền (Sprint 5)

Sau khi đăng nhập, giao diện hiện **dashboard** gồm 3 thẻ thống kê (tổng vùng
trồng, tổng lô nông sản, tổng sản lượng kg) và 2 bảng dữ liệu có cột **Thao tác**:

| Tài khoản | Thêm | Sửa | Xoá | Quản lý tài khoản |
| --- | --- | --- | --- | --- |
| `admin` | ✅ | ✅ | ✅ | ✅ (`GET /users`) |
| `farmer` | ✅ | ✅ | ❌ **không có nút Xoá** (cố gọi API xoá → `403`) | ❌ |

- Bấm **Sửa** ở bảng → form phía trên tự điền dữ liệu và chuyển sang chế độ sửa
  (nút đổi thành **Cập nhật...**); bấm **Huỷ sửa** để quay lại chế độ thêm mới.
- Bấm **Xoá** → hộp thoại xác nhận → gọi `DELETE`. Xoá **vùng trồng** sẽ xoá kèm
  toàn bộ lô nông sản của vùng đó (backend trả về số lô bị xoá kèm để hiển thị).
- **Chưa đăng nhập:** chỉ hiện màn hình đăng nhập — dashboard, bảng dữ liệu và
  form nhập bị ẩn hoàn toàn. Bấm **Đăng xuất** → xoá phiên, xoá dữ liệu đang hiện
  và quay về màn hình đăng nhập.
- API tương ứng: `POST` / `GET` / `PUT` / `DELETE` cho `/farms` và `/batches`
  (bảng endpoint đầy đủ: xem `backend/README.md`, mục 3).

## Các module nâng cao & Kiểm thử chất lượng (Sprint 1)

### 1. Chuỗi sự kiện bất biến (Immutable Hash-Chain - SCRUM-39 & SCRUM-44)
- **`POST /batches/{id}/events`**: Ghi thêm sự kiện (Append-only). Mỗi bản ghi tự động liên kết mã băm SHA-256 với sự kiện liền trước (`previous_hash`).
- **`GET /batches/{id}/events`**: Lấy dòng thời gian truy xuất và tự động quét toàn bộ chuỗi để phát hiện sửa lén (`tampered_index`).

### 2. Thuật toán duyệt phả hệ BFS & Benchmark đếm tay (SCRUM-65 & SCRUM-69, T-52 & T-53)
- **Thuật toán BFS** (`backend/app/lineage.py`): Duyệt ngược cây nguồn gốc từ con lên cha và duyệt xuôi từ cha xuống hậu duệ theo từng tầng, có cơ chế ngắt và phát hiện chu trình (`LineageCycleError`).
- **Script dựng đồ thị mẫu** (`backend/scripts/seed_lineage_graph.py`): Dùng chính hàm `split_batch` và `merge_batches` của hệ thống để dựng đồ thị 4 tầng (18 lô) qua 3 tổ chức (Vùng Trồng, Hợp Tác Xã, Tổng Kho Phân Phối).
- **Bộ benchmark chuẩn & Đáp án tính sẵn bằng tay** (`backend/data/lineage_benchmark.json`): Hai người đếm độc lập (Nguyễn Thanh Sơn & Lê Hoàng Quân) và đối chiếu khớp 100% cho cả tổ tiên và hậu duệ.

#### Sơ đồ cấu trúc đồ thị phả hệ mẫu (ASCII Art):
```text
[TẦNG 1: THU HOẠCH - VÙNG TRỒNG CAO LÃNH]
   F0-01 (1,200kg)              F0-02 (1,200kg)              F0-03 (1,200kg)
     │                           │                           │
     ├─────────────┐             ├─────────────┐             ├─────────────┐
     ▼             ▼             ▼             ▼             ▼             ▼
[TẦNG 2: PHÂN LOẠI & TÁCH LÔ - HỢP TÁC XÃ SƠ CHẾ MỸ XƯƠNG]
   S1.01(500kg)  S1.02(500kg)   S2.01(500kg)  S2.02(500kg)   S3.01(500kg)  S3.02(500kg)
     │             │             │             │             │             │
     │             │             │             │             │             │
     └────────┐    │             │             │             │    ┌────────┘
              │    │             │             │             │    │
              ▼    │             ▼             ▼             ▼    ▼
[TẦNG 3: GỘP CHÉO - HỢP TÁC XÃ SƠ CHẾ MỸ XƯƠNG]
         M01 (500kg)                  M02 (500kg)                  M03 (500kg)
     (250kg từ S1.01 +           (250kg từ S2.02 +           (250kg từ S3.02 +
      250kg từ S2.01)             250kg từ S3.01)             250kg từ S1.02)
              │                            │                            │
        ┌─────┴─────┐                ┌─────┴─────┐                ┌─────┴─────┐
        ▼           ▼                ▼           ▼                ▼           ▼
[TẦNG 4: THÀNH PHẨM XUẤT XƯỞNG - TỔNG KHO PHÂN PHỐI NÔNG SẢN MIỀN NAM]
    FIN1.01      FIN1.02         FIN2.01      FIN2.02         FIN3.01      FIN3.02
    (200kg)      (200kg)         (200kg)      (200kg)         (200kg)      (200kg)
```

### 3. Nghiệp vụ Tách lô & Phân quyền tổ chức (T-40 / T-41 / SCRUM-56 / SCRUM-57)
- **Cơ chế Khóa dòng bi quan (`SELECT ... FOR UPDATE`)**: Bảo vệ số dư lô mẹ trước race condition khi nhiều luồng cùng gọi tách lô song song.
- **Số học Decimal cố định**: Sử dụng `to_fixed_decimal` với 4 chữ số thập phân (`0.0001` kg), triệt tiêu hoàn toàn sai số floating-point IEEE 754.
- **Bộ kiểm thử 4 Kịch bản Nghiệm thu (Acceptance Criteria - AC)** (`tests/test_batch_split_multiline.py`):
  1. *AC 1:* Lô 100 kg tách thành 3 lô con (30, 30, 40 kg) $\rightarrow$ 3 lô con có mã riêng, ghi rõ `parent_id`, lô mẹ còn đúng 0 kg.
  2. *AC 2:* Tách 100 kg thành 30 và 30 kg $\rightarrow$ Lô mẹ còn 40 kg và vẫn tiếp tục tách tiếp được.
  3. *AC 3:* Lỗi phát sinh ở lô con thứ 2 $\rightarrow$ Toàn bộ giao dịch rollback, không có lô con nào được tạo và lô mẹ giữ nguyên 100 kg.
  4. *AC 4:* Lô không do tổ chức của người dùng nắm giữ $\rightarrow$ Trả về HTTP 403 Forbidden.

### 4. Nghiệp vụ Gộp lô & Phả hệ N-N (T-44 / T-46 / SCRUM-60 / SCRUM-62)
- **Chống Deadlock bằng Thứ tự khóa toàn cục (Global Lock Ordering)**: Luôn khóa các lô mẹ theo thứ tự ID tăng dần (`sorted(item.batch_id)`), loại trừ chu kỳ chờ đợi (Circular Wait).
- **Ràng buộc kiểm tra nghiêm ngặt**:
  - *Tính đồng nhất sản phẩm*: Chặn ngay (HTTP 400 Bad Request) khi cố gộp các lô khác loại sản phẩm.
  - *Thẩm quyền tổ chức*: Chặn ngay (HTTP 403 Forbidden) nếu có bất kỳ lô nào không do tổ chức hiện tại nắm giữ.
  - *Bảo toàn vật chất*: Dùng số học `Decimal` đảm bảo tổng khối lượng lô mới bằng đúng tổng khối lượng đã trừ từ các lô mẹ.
  - *Lưu vết phả hệ*: Tự động tạo bản ghi `BatchRelation` trong CSDL và sự kiện liên kết mật mã `MERGE` / `MERGE_PARENT`.
- **Bộ kiểm thử 4 Kịch bản Nghiệm thu (Acceptance Criteria - AC)** (`tests/test_batch_merge_acceptance.py`):
  1. *AC 1:* Gộp toàn bộ 3 lô cùng sản phẩm $\rightarrow$ Lô mới ghi rõ cả 3 lô mẹ, cả 3 lô mẹ còn lại 0 kg.
  2. *AC 2:* Gộp một phần của mỗi lô $\rightarrow$ Lô mới có khối lượng bằng tổng phần lấy, mỗi lô mẹ trừ đúng phần đó.
  3. *AC 3:* Hai lô khác sản phẩm $\rightarrow$ Bị chặn kèm lý do chi tiết.
  4. *AC 4:* Một trong các lô không do tổ chức tôi giữ $\rightarrow$ Bị chặn (HTTP 403 Forbidden).

### 5. Phân giải tổ chức đa người dùng (Multi-tenant - SCRUM-27..29)
- ContextVar tự động tiêm điều kiện lọc `organization` cho các câu truy vấn, đảm bảo các bên trong chuỗi cung ứng không xem chéo dữ liệu của nhau.

### 6. Hướng dẫn chạy kiểm thử tự động
```powershell
$env:PYTHONPATH='backend;tests'
python -m pytest tests/ -v
```
Toàn bộ test suite kiểm tra song song (concurrency), kiểm chứng phả hệ (lineage) và cô lập đa tổ chức (tenant isolation) đều chạy xanh 100%.
