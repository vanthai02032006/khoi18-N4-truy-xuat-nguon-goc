# TTCS-K18C4-N4
Dự án TTCS K18C4 - Truy xuất nguồn gốc và giám sát chuỗi lạnh nông sản

## Cấu trúc thư mục

| Thư mục | Nội dung |
| --- | --- |
| `backend/` | FastAPI + SQLite + SQLAlchemy (chi tiết xem `backend/README.md`) |
| `frontend/` | Demo giao diện: `index.html`, `css/style.css`, `js/app.js` — HTML5 + CSS + JavaScript thuần, không framework |
| `docs/` | Tài liệu dự án |

## Chạy test backend

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pip install pytest
$env:PYTHONPATH = "backend"
pytest tests -v
```

Đúng bằng 2 lệnh mà pipeline CI chạy (`flake8 backend --select=E9,F63,F7,F82` và
`PYTHONPATH=backend pytest backend/tests -v`), không cần dịch vụ CSDL ngoài.

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

### 2. Thuật toán duyệt phả hệ BFS & Benchmark đếm tay (SCRUM-65 & SCRUM-69)
- **Thuật toán BFS** (`backend/app/lineage.py`): Duyệt ngược cây nguồn gốc từ con lên cha theo từng tầng, có cơ chế ngắt và phát hiện chu trình (`LineageCycleError`).
- **Bộ benchmark chuẩn** (`backend/data/lineage_benchmark.json`): Đối chiếu 100% với đáp án đếm tay độc lập của nhóm.

### 3. Phân giải tổ chức đa người dùng (Multi-tenant - SCRUM-27..29)
- ContextVar tự động tiêm điều kiện lọc `organization` cho các câu truy vấn, đảm bảo các bên trong chuỗi cung ứng không xem chéo dữ liệu của nhau.

### 4. Hướng dẫn chạy kiểm thử tự động
```powershell
$env:PYTHONPATH='backend'
python -m pytest tests/ -v
```
Toàn bộ test suite kiểm tra song song (concurrency), kiểm chứng phả hệ (lineage) và cô lập đa tổ chức (tenant isolation) đều chạy xanh 100%.

### Bàn giao quyền giữ lô (API, chưa có giao diện)

Nghiệp vụ chuyển quyền giữ một lô nông sản sang đơn vị khác trong chuỗi cung ứng:

| Bước | Endpoint | Ai gọi được | Kết quả |
| --- | --- | --- | --- |
| Tạo phiếu | `POST /handovers` | đã đăng nhập | phiếu `pending`; lô **vẫn thuộc bên giao** |
| Xác nhận | `POST /handovers/{id}/accept` | **chỉ bên nhận** | đổi "tổ chức đang giữ" lô sang bên nhận + ghi **2 sự kiện** |
| Từ chối | `POST /handovers/{id}/reject` | **chỉ bên nhận** | giữ nguyên bên giao, lưu **lý do từ chối** (bắt buộc) |

- Ai không phải bên nhận gọi xác nhận/từ chối đều nhận **`403 Forbidden`** — kể cả
  `admin`; quyền được kiểm tra **ở máy chủ**, không phải chỉ ẩn trên giao diện.
- Mỗi thao tác chạy trong **một transaction**: lỗi giữa chừng thì không đổi chủ
  sở hữu và không ghi sự kiện nào (có test rollback).
- Từ chối mà không nhập lý do → **`422`**.
- Sự kiện bàn giao được ghi vào **cùng chuỗi băm** với `POST /batches/{id}/events`
  (dùng `compute_event_hash`), nên `GET /batches/{id}/events` kiểm tra được toàn vẹn.
- Chi tiết đầy đủ (luồng, sự kiện, cấu trúc bảng): xem `backend/README.md`, mục
  **"Bàn giao quyền giữ lô (Handover)"**.
- Giao diện frontend cho nghiệp vụ này **chưa được làm** (ngoài phạm vi task).
