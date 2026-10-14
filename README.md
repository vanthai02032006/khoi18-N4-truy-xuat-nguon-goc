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

### 2. Thuật toán duyệt phả hệ BFS & Benchmark đếm tay (SCRUM-65 & SCRUM-69)
- **Thuật toán BFS** (`backend/app/lineage.py`): Duyệt ngược cây nguồn gốc từ con lên cha theo từng tầng, có cơ chế ngắt và phát hiện chu trình (`LineageCycleError`).
- **Bộ benchmark chuẩn** (`backend/data/lineage_benchmark.json`): Đối chiếu 100% với đáp án đếm tay độc lập của nhóm.

### 3. Phân giải tổ chức đa người dùng (Multi-tenant - SCRUM-27..29)
- ContextVar tự động tiêm điều kiện lọc `organization` cho các câu truy vấn, đảm bảo các bên trong chuỗi cung ứng không xem chéo dữ liệu của nhau.

### 4. Hướng dẫn chạy kiểm thử tự động
```powershell
$env:PYTHONPATH='backend;tests'
python -m pytest tests/ -v
```
Bộ test nằm ở `tests/` tại gốc repo và import trực tiếp `conftest`, nên `PYTHONPATH` phải có **cả** `backend` lẫn `tests` (trên Linux/macOS dùng dấu `:` thay cho `;`).

Toàn bộ test suite kiểm tra song song (concurrency), kiểm chứng phả hệ (lineage) và cô lập đa tổ chức (tenant isolation) đều chạy xanh 100%.

### 4.1. CI Pipeline (GitHub Actions)
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) tự chạy khi mở Pull Request vào `main`/`develop` (và khi push vào 2 nhánh này), gồm 2 job bắt buộc theo ruleset của nhóm:

| Job | Nội dung |
| --- | --- |
| `Backend Quality & Tests` | `flake8` (E9/F63/F7/F82) + `pytest tests` |
| `Security & Compliance Scan` | Quét secret (Gitleaks), phát hiện log PII nông hộ, `pip-audit` phụ thuộc |


### 5. Địa điểm / cửa hàng & vị trí hiện tại của lô (S-18 — ước lượng thô 3 SP)

- **Story:** *Là nhà phân phối, tôi muốn ghi nhận lô đang nằm ở cửa hàng nào trong hệ thống của tôi để thu hồi chỉ đúng cửa hàng thay vì lục cả chuỗi.*
- **Bảng dữ liệu:** `locations` (`organization`, `name`, `address` — tên duy nhất trong một tổ chức) và cột `batches.current_location_id`.
- **Endpoint:** `POST /locations` (khai báo địa điểm thuộc tổ chức tôi), `GET /locations` (chỉ địa điểm của tổ chức tôi), `POST /batches/{id}/location` (ghi nhận lô đang ở cửa hàng nào + sự kiện `LOCATION_CHANGED` vào chuỗi băm), `GET /locations/{id}/batches` (tra cứu thu hồi đúng điểm bán). Phép tách ở S-17 nay nhận `location_id` trên từng dòng lô con.
- **Kiểm thử:** [`tests/test_batch_location_tracking.py`](tests/test_batch_location_tracking.py) — 7 ca bám sát 3 tiêu chí nghiệm thu (khai báo địa điểm thuộc tổ chức tôi, chuyển lô ghi địa điểm + sự kiện, tách lô cho nhiều cửa hàng) kèm các ca bị chặn (`403`/`404`/`400`).

### 6. Đánh dấu lô có sự cố & mở lệnh thu hồi (S-19 — ước lượng thô 3 SP)

- **Story:** *Là cán bộ kiểm tra, tôi muốn đánh dấu một lô có sự cố và mở lệnh thu hồi để bắt đầu quy trình xử lý ngay.*
- **Endpoint:** `POST /batches/{id}/incident` (inspector **hoặc** admin) — đánh dấu lô có sự cố kèm lý do, tạo **lệnh thu hồi có mã riêng** (`ORD-REC-xxxx`) gắn lô và ghi sự kiện `INCIDENT_FLAGGED` vào chuỗi băm bất biến. Vai trò khác gọi → `403`; lô đã có lệnh `IN_PROGRESS` → `409` kèm mã lệnh đang mở (`open_order_code`) để tránh mở trùng.
- **Kiểm thử:** [`tests/test_batch_incident_recall.py`](tests/test_batch_incident_recall.py) — 7 ca bám sát 3 tiêu chí nghiệm thu (tạo lệnh + sự kiện vào chuỗi, `403` với vai trò khác, `409` trỏ tới lệnh đang mở) kèm ca biên `401`/`404` và mở lệnh mới sau khi lệnh cũ hoàn tất.

### 7. Kế hoạch thu hồi theo lô & cửa hàng (S-20 — ước lượng thô 3 SP)

- **Story:** *Là nhà phân phối, tôi muốn biết chính xác lô nào ở cửa hàng nào và còn bao nhiêu để thu hồi đúng chỗ thay vì lục cả kho.*
- **Bảng dữ liệu:** `recall_order_batches` (lệnh thu hồi ↔ nhiều lô) và cột `batches.consumed_quantity` (khối lượng đã bán cho người tiêu dùng — phần còn lại nằm ở `batches.quantity`).
- **Endpoint:** `POST /orders` nhận `batch_ids` (nhiều lô) hoặc `product_name` (gom mọi lô cùng sản phẩm) và ghi sự kiện `RECALL_ISSUED` vào chuỗi băm của từng lô; `GET /orders/{id}/recovery-plan` trả **từng lô kèm tổ chức, địa điểm, khối lượng còn lại và khối lượng đã tiêu thụ**, phần tổng hợp **còn thu hồi được vs đã ra thị trường** (`recallable_quantity`/`consumed_quantity`/`recallable_ratio`) và danh sách lô **gom theo địa điểm** để đi thu hồi từng chỗ một; `POST /batches/{id}/consume` nhận `{"quantity": n}` để ghi **bán một phần** (bỏ trống = bán hết như trước).
- **Kiểm thử:** [`tests/test_recall_recovery_plan.py`](tests/test_recall_recovery_plan.py) — 10 ca bám sát 2 tiêu chí nghiệm thu (lệnh 20 lô ở 3 cửa hàng, bán một phần/bán hết, gom theo tên sản phẩm, sự kiện ghi cho từng lô) kèm các ca bị chặn (`400`/`401`/`404`).

### 8. Đóng băng lô đang bị thu hồi (S-21 — ước lượng thô 2 SP)

- **Story:** *Là cán bộ kiểm tra tôi muốn lô đang bị thu hồi không di chuyển được nữa để vùng ảnh hưởng không lan rộng trong lúc xử lý.*
- **Bảng dữ liệu:** cờ `batches.incident_flagged` kèm `incident_order_id` / `incident_order_code` / `incident_flagged_at` (tự migrate cho DB cũ: dựng lại cờ từ sự kiện `INCIDENT_FLAGGED` của S-19 và `RECALL_ISSUED` của S-20).
- **Endpoint:** `POST /batches/{id}/handover/initiate`, `POST /batches/{id}/handover/accept`, `POST /batches/{id}/split`, `POST /batches/merge` và `POST /batches/{id}/location` trả **`409` kèm mã lệnh thu hồi** khi lô đang nằm trong lệnh thu hồi đang mở (lệnh một lô ở S-19 hoặc lệnh nhiều lô ở S-20); lệnh đã **hoàn tất** thì vẫn bị chặn vì lô đã được đánh dấu có sự cố. `GET /batches/{id}` trả thêm `incident_flagged`/`incident_order_code` để giao diện chặn thao tác trước khi gọi API.
- **Kiểm thử:** [`tests/test_recall_batch_lock.py`](tests/test_recall_batch_lock.py) — 7 ca bám sát 2 tiêu chí nghiệm thu (chặn kèm mã lệnh khi lệnh đang mở, đóng băng cả phạm vi lệnh nhiều lô, vẫn chặn sau khi lệnh hoàn tất, bàn giao đang chờ không hoàn tất được) kèm ca biên: thao tác bị chặn không đổi khối lượng / người giữ / địa điểm / chuỗi sự kiện và lô ngoài phạm vi lệnh vẫn di chuyển bình thường.

### 9. Kịch bản thu hồi mẫu & đáp án đếm tay (S-22 — ước lượng thô 2 SP)

- **Story:** *Là thành viên phát triển tôi muốn một kịch bản thu hồi mẫu có đáp án đếm tay để thuật toán liệt kê thu hồi được kiểm bằng đáp án độc lập.*
- **Thuật toán:** `enumerate_recall_scope()` / `build_recall_scope()` trong `backend/app/recall_enumeration.py` — duyệt BFS xuôi từ lô gốc, mỗi lô hậu duệ kèm **khối lượng còn lại**, khối lượng đã bán, tổ chức đang giữ và **tầng** của nó; trong cùng một tầng sắp theo mã lô nên kết quả ổn định (deterministic) để đối chiếu với đáp án. Hàm **thuần** (chỉ cần quan hệ + số liệu) tách khỏi hàm đọc CSDL, nhờ vậy đáp án đếm tay kiểm được thuật toán một cách độc lập, không phụ thuộc dữ liệu trong máy.
- **Script dựng kịch bản:** `cd backend; .\.venv\Scripts\python.exe scripts\seed_recall_sample.py` — dựng đồ thị **bốn tầng qua ba tổ chức** (HTX → nhà máy chế biến → chuỗi siêu thị) bằng chính hàm tách/gộp của ứng dụng: 18 lô, 6 quan hệ gộp **từ nhiều nguồn**, lô `LOT-S22-F12` **đã bán hết** và `LOT-S22-F22` bán một phần. Script tự dọn dữ liệu mẫu cũ rồi mới dựng lại nên chạy nhiều lần vẫn cho kết quả như cũ, và in luôn phạm vi thu hồi của từng lô gốc để người đọc so tay với tệp đáp án.
- **Tệp đáp án:** [`backend/data/recall_benchmark.json`](backend/data/recall_benchmark.json) — tra lô gốc ra danh sách hậu duệ kèm khối lượng còn lại từng lô; **hai người đếm độc lập** (`Nguyễn Thanh Sơn (Lead)` và `Lê Hoàng Quân`) ghi hai bảng đếm riêng rồi chốt lại phần `ground_truth_recall_scope` dùng để kiểm thuật toán.
- **Kiểm thử:** [`tests/test_recall_enumeration_benchmark.py`](tests/test_recall_enumeration_benchmark.py) — 10 ca bám sát 2 tiêu chí nghiệm thu (đồ thị 4 tầng/3 tổ chức, có lô gộp nhiều nguồn, có lô đã tiêu thụ; thuật toán khớp 100% đáp án theo **cả** đường đọc CSDL **lẫn** đường hàm thuần) kèm ca bảo toàn khối lượng, chạy lại script không nhân đôi dữ liệu và ca biên (lô cuối chuỗi không có hậu duệ, thiếu số liệu lô gốc → báo lỗi rõ ràng).
