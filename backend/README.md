# Backend - TTCS K18C4

Backend API cho đề tài **"Truy xuất nguồn gốc và giám sát chuỗi lạnh nông sản"**.

- **Framework:** Python + FastAPI
- **Database:** SQLite (file local, không cần cài server)
- **ORM:** SQLAlchemy 2.0
- **Sprint 1 (nền tảng):** cấu hình FastAPI, kết nối SQLite, endpoint kiểm tra
  hệ thống `GET /health`.
- **Sprint 2:** module **Farm** — quản lý vùng trồng (`POST /farms`, `GET /farms`).
- **Sprint 3:** module **Batch** — quản lý lô nông sản (`POST /batches`,
  `GET /batches`, `GET /batches/{batch_id}`).
  Quan hệ dữ liệu: `Farm 1 ---- N Batch`.
- **Sprint 4:** **đăng nhập + phân quyền cơ bản** — bảng `users`, `POST /auth/login`,
  `GET /users` (chỉ admin) và dependency `require_admin` / `require_farmer`.
  *Không dùng JWT*: API cần quyền xác thực bằng **HTTP Basic**
  (Swagger UI có sẵn nút **Authorize**).
- **Sprint 5:** hoàn thiện **CRUD đầy đủ** — bổ sung `PUT` / `DELETE`
  cho `/farms` và `/batches` (xoá **chỉ dành cho `admin`**) kèm schema
  `FarmUpdate`/`BatchUpdate`/`DeleteResponse`.
- **Task T-49 (SCRUM-65) & T-54 (truy vết & phân quyền):**
  - **T-49:** Endpoint `GET /batches/{batch_id}/trace` (kèm alias `/lineage`, `/origin`) trả về kết quả truy vết nguồn gốc kèm **lô gốc (`root_batch`)**, **thông tin vùng trồng của lô gốc (`origin_farm`)**, và danh sách phả hệ phân theo từng tầng (`lineage`).
  - **T-54:** Chính sách bảo vệ quyền xem riêng tư cho lô nông sản (`is_restricted`). Lô không có quyền xem bị trả về mã lỗi **`403 Forbidden`**.
  - **Cache 60 giây:** Kết quả truy vết được lưu đệm in-memory thread-safe trong 60 giây theo mã lô (tự động invalidate khi cập nhật hoặc xoá lô).
  - **Frontend:** Modal chi tiết & mục 'Nguồn gốc' hiển thị danh sách theo tầng trực quan, đếm ngược cache 60s, và khung cảnh báo đỏ khi nhận 403 Forbidden.
- **Task T-19 (SCRUM-35) — Màn hình ghi nhận thu hoạch, hiện mã lô cỡ chữ lớn:**
  - **Màn hình thu hoạch T-15:** Biểu mẫu 4 trường bắt buộc: Thửa đất xuất xứ (`farm_id`), Loại nông sản (`product_name`), Sản lượng thu hoạch kg (`quantity`), Ngày thu hoạch (`harvest_date`). Hỗ trợ chọn lô cha (T-49) và gắn cờ bảo mật (T-54).
  - **Mã lô sinh tự động:** Định dạng chuẩn T-19 `LOT-{farm_id:02d}-{YYYYMMDD}-{sequence:02d}` (tự động tăng số thứ tự sequence cho các đợt thu hoạch cùng ngày từ cùng một thửa).
  - **Hiển thị Mã Lô Cỡ Chữ Lớn (Giant Batch Code):** Sau khi lưu, bật modal nổi bật `#harvest-success-modal` hiển thị mã lô cỡ chữ khổng lồ (`clamp(2rem, 6.2vw, 2.9rem)`), font Monospace.
  - **Tối ưu ngoài trời nắng gắt (Outdoor Sunlight UX):** Bố cục tương phản cực cao (> 14:1 WCAG AAA), nền xanh than chì rêu đậm (`#064e3b` / `#022c22`), chữ hổ phách huỳnh quang rực rỡ (`#fbbf24`), kèm hiệu ứng text-shadow dạ quang, triệt tiêu hiện tượng lóa nắng trên màn hình điện thoại.
  - **Nút sao chép cỡ lớn 1-click:** Nút `#btn-copy-giant-code` touch target rộng (chiều cao 56px, chữ 1.15rem), phản hồi xúc giác/thị giác lập tức `✓ ĐÃ SAO CHÉP MÃ LÔ!` và toast thông báo.
  - **Tối ưu thực địa trên điện thoại:** Responsive chuẩn cho màn hình di động ngoài ruộng vườn (touch targets >= 52px, nút bấm full-width, điều hướng "Tiếp tục ghi nhận lô mới" hoặc "Xem phả hệ T-49").
  - **Kiểm thử tự động:** `backend/tests/test_t19_harvest_batch_code.py` đạt 100%.
- **Task T-19, T-39 & S-17 (Hàm tách lô nông sản transactional):**
  - **T-19:** Hàm sinh mã lô nông sản tự động theo quy chuẩn `LOT-{farm_id:02d}-P{parent_id}.{sequence:02d}-{YYYYMMDD}` (lô con) hoặc `LOT-{farm_id:02d}-{YYYYMMDD}-{sequence:02d}` (lô gốc F0).
  - **T-39 (SCRUM-55):** Ghi nhận quan hệ phân cấp cây phả hệ (`parent_id = parent.id`), trừ chính xác khối lượng khả dụng của lô mẹ.
  - **Kế thừa nguồn gốc 100%:** Lô con tự động kế thừa loại sản phẩm (`product_name`) và vùng trồng (`farm_id`, `harvest_date`) của lô mẹ, không cho phép nhập sai lệch.
  - **S-17:** Hàm tách mở transaction nguyên tử, vượt qua cả 3 ca kiểm thử (tách 1 phần, tách toàn bộ, bắt lỗi biên/vượt khối lượng); cơ chế rollback sạch sẽ 100% nếu phát sinh lỗi ở lô con thứ hai.
  - **API:** Endpoint `POST /batches/{batch_id}/split` hỗ trợ tách lô nhanh chóng kèm phân quyền `farmer`/`admin`.
- **Task T-29 (SCRUM-45) (Cơ chế chống sửa lén bản ghi bằng Hash Chain):**
  - **Mô hình Cryptographic Hash Chain:** Loại bỏ blockchain phức tạp, sử dụng chuỗi băm mật mã SHA-256 lưu trực tiếp trong bảng `batch_events`. Mỗi bản ghi băm nội dung kèm `prev_hash` của bản ghi trước.
  - **Nghiệm thu 3 ca kiểm thử:**
    1. *Ca 1 (Sửa lén 1 bản ghi bằng SQL):* Phát hiện `DATA_MODIFIED` 100% do hash lưu trữ không khớp với hash nội dung mới.
    2. *Ca 2 (Xoá 1 bản ghi bằng SQL):* Phát hiện `RECORD_DELETED_OR_CHAIN_BROKEN` 100% do chuỗi hash bị đứt đoạn.
    3. *Ca 3 (Giữ nguyên vẹn 10 sự kiện):* Khẳng định chuỗi `VERIFIED` toàn vẹn 100%.
  - **Tương thích CI:** Tích hợp bộ test pytest chạy độc lập, tự dọn dẹp không cần can thiệp thủ công.
  - **API:** `POST /batches/{id}/events`, `GET /batches/{id}/events`, `GET /batches/{id}/events/verify`.

---

## 1. Cấu trúc thư mục

```
backend/
├── app/
│   ├── __init__.py          # Đánh dấu package + khai báo __version__
│   ├── main.py              # Khởi tạo FastAPI, CORS, lifespan, đăng ký router
│   ├── database.py          # Engine SQLite, SessionLocal, Base, get_db, init_db, upgrade_db_schema
│   ├── cache.py             # TraceCache in-memory thread-safe TTL 60s (T-49)
│   ├── batch_split.py       # Logic sinh mã T-19 & hàm tách lô nguyên tử split_batch (T-39, S-17)
│   ├── event_chain.py       # Cơ chế Cryptographic Hash Chain & chống sửa lén bản ghi (T-29 / SCRUM-45)
│   ├── models.py            # ORM models: Farm, Batch, BatchEvent (T-29), User
│   ├── schemas.py           # Pydantic: Health, Farm, Batch, BatchEvent, TraceResponse, VerifyResponse...
│   ├── security.py          # Băm mật khẩu, xác thực Basic, kiểm tra quyền xem T-54 (403 Forbidden)
│   └── routers/
│       ├── __init__.py      # Export các router
│       ├── health.py        # GET /health
│       ├── auth.py          # POST /auth/login (Sprint 4)
│       ├── users.py         # GET /users - chỉ admin (Sprint 4)
│       ├── farms.py         # CRUD /farms: POST, GET, PUT {id}, DELETE {id} (Sprint 5)
│       └── batches.py       # CRUD /batches + trace (T-49) + split (S-17) + events & verify (T-29)
├── tests/
│   ├── __init__.py
│   └── test_t29_tamper_proofing.py # Bộ kiểm thử CI (pytest) nghiệm thu 3 ca T-29
├── test_split_s17.py        # Bộ kiểm thử nghiệm thu 3 ca S-17 & rollback sạch sẽ ở lô thứ 2
├── test_t29_tamper_proofing.py # Script kiểm thử độc lập 3 ca chống sửa lén T-29
├── test_t49_t54.py          # Script kiểm thử tự động toàn diện T-49, T-54 và cache 60s
├── requirements.txt         # Danh sách thư viện Python
├── .gitignore               # Bỏ qua file DB, __pycache__, .venv...
└── README.md                # Tài liệu này
```

> File `ttcs.db` (SQLite) sẽ được **tự sinh** trong thư mục `backend/` ở lần
> chạy đầu tiên — không cần commit file này lên Git.

---

## 2. Cách chạy

### Bước 1 — Tạo môi trường ảo (khuyến nghị)

Mở PowerShell tại thư mục `backend/`:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> Nếu PowerShell báo lỗi `Activate.ps1 cannot be loaded`, chạy trước:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
> (hoặc dùng `cmd`: `.venv\Scripts\activate.bat`)

### Bước 2 — Cài thư viện

```powershell
pip install -r requirements.txt
```

### Bước 3 — Chạy server

```powershell
uvicorn app.main:app --reload
```

> Bắt buộc chạy lệnh ở **thư mục `backend/`** vì code dùng absolute import
> dạng `from app.database import ...`.

Server mặc định: <http://127.0.0.1:8000>

### Bước 4 — Kiểm tra

| Mục | Địa chỉ |
| --- | --- |
| Health check | <http://127.0.0.1:8000/health> |
| Swagger UI | <http://127.0.0.1:8000/docs> |
| ReDoc | <http://127.0.0.1:8000/redoc> |

Kiểm tra nhanh bằng `curl`:

```powershell
curl.exe http://127.0.0.1:8000/health
```

Kết quả mong đợi:

```json
{ "status": "running" }
```

Hoặc chạy không cần bật sẵn server (dùng TestClient của FastAPI):

```powershell
pip install httpx
python -c "from fastapi.testclient import TestClient; from app.main import app; c = TestClient(app); print(c.get('/health').status_code, c.get('/health').json())"
```

Kết quả mong đợi: `200 {'status': 'running'}`

### Bước 5 — Đăng nhập (Sprint 4)

Tài khoản demo được tạo tự động ở lần chạy đầu tiên:

| Tài khoản | Mật khẩu | Vai trò |
| --- | --- | --- |
| `admin` | `123456` | admin (toàn quyền, xem được `GET /users`) |
| `farmer` | `123456` | farmer (quản lý vùng trồng + lô nông sản) |

Kiểm tra nhanh bằng `curl.exe`:

```powershell
# Đăng nhập đúng -> 200 + {"username":"admin","role":"admin"}
curl.exe --% -s -X POST http://127.0.0.1:8000/auth/login -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"123456\"}"

# Không gửi thông tin đăng nhập -> 401 (GET /farms nay cần quyền)
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:8000/farms

# Gửi kèm Basic auth -> 200
curl.exe -s -u admin:123456 http://127.0.0.1:8000/farms
```

> Sau khi đăng nhập ở bước này, mở <http://127.0.0.1:8000/docs> và bấm nút
> **Authorize** để test các API cần quyền ngay trên Swagger UI.
>
> **Sprint 5 — quyền xoá dữ liệu:** chỉ tài khoản **`admin`** gọi được
> `DELETE /farms/{id}` và `DELETE /batches/{id}`. Tài khoản `farmer` vẫn
> thêm/sửa dữ liệu nông sản nhưng sẽ nhận **`403 Forbidden`** khi xoá, và trên
> giao diện frontend thì **nút Xoá không hiện** với farmer.

---

## 3. API hiện có

| Method | Endpoint | Mô tả | Quyền | Mã trả về |
| --- | --- | --- | --- | --- |
| GET | `/health` | Kiểm tra hệ thống đang chạy | công khai | `200` |
| POST | `/auth/login` | Kiểm tra tài khoản, trả về `{username, role}` | công khai | `200` · `401` sai tài khoản · `422` thiếu dữ liệu |
| GET | `/farms` | Lấy danh sách vùng trồng (sắp xếp theo `id` tăng dần) | farmer **hoặc** admin | `200` · `401` |
| POST | `/farms` | Tạo vùng trồng mới | farmer **hoặc** admin | `201` · `401` · `422` dữ liệu sai |
| PUT | `/farms/{farm_id}` | Cập nhật (thay thế) vùng trồng theo `id` | farmer **hoặc** admin | `200` · `401` · `404` không tìm thấy · `422` dữ liệu sai |
| DELETE | `/farms/{farm_id}` | Xoá vùng trồng **và các lô nông sản của nó** | **chỉ admin** | `200` · `401` · `403` sai vai trò · `404` không tìm thấy |
| GET | `/batches` | Lấy danh sách lô nông sản (sắp xếp theo `id` tăng dần) | công khai | `200` |
| GET | `/batches/{batch_id}` | Xem chi tiết một lô nông sản | công khai | `200` · `404` không tìm thấy |
| POST | `/batches` | Tạo lô nông sản (kiểm tra `farm_id` tồn tại) | farmer **hoặc** admin | `201` · `401` · `404` farm không tồn tại · `422` dữ liệu sai |
| PUT | `/batches/{batch_id}` | Cập nhật (thay thế) lô nông sản - đổi được `farm_id` nếu tồn tại | farmer **hoặc** admin | `200` · `401` · `404` lô/farm không tồn tại · `422` |
| DELETE | `/batches/{batch_id}` | Xoá một lô nông sản | **chỉ admin** | `200` · `401` · `403` sai vai trò · `404` không tìm thấy |
| GET | `/batches/{batch_id}/trace` | Truy vết nguồn gốc phả hệ, lô gốc & vùng trồng gốc (cache 60s) | T-54 (bảo vệ nếu restricted) | `200` · `403` không có quyền · `404` |
| POST | `/batches/{batch_id}/split` | Tách lô nông sản con transactional (T-19, T-39, S-17) | farmer **hoặc** admin | `201` · `400` vượt khối lượng · `403` · `404` |
| POST | `/batches/{batch_id}/events` | Ghi nhận sự kiện chuỗi cung ứng mật mã băm SHA-256 (T-29) | farmer **hoặc** admin | `201` · `401` · `403` · `404` |
| GET | `/batches/{batch_id}/events` | Lấy danh sách sự kiện theo chuỗi băm của lô (T-29) | công khai (hoặc T-54) | `200` · `403` · `404` |
| GET | `/batches/{batch_id}/events/verify` | Kiểm tra tính toàn vẹn & phát hiện sửa lén / xoá bản ghi (T-29) | công khai (hoặc T-54) | `200` (is_valid: true/false) · `403` · `404` |
| GET | `/users` | Danh sách tài khoản (không kèm mật khẩu) | **chỉ admin** | `200` · `401` · `403` sai vai trò |

> ✅ **Sprint 5 hoàn thiện CRUD:** cả Farm và Batch đều có đủ `POST` / `GET` /
> `GET {id}` (Batch) / `PUT` / `DELETE`. `PUT` là cập nhật **thay thế**: client
> gửi đầy đủ các trường như khi tạo mới, thiếu trường → `422`.
>
> ⚠️ **Thay đổi so với Sprint 3/4:** `PUT` / `DELETE` là endpoint **mới**, trong
> đó nhóm `DELETE` **chỉ admin** gọi được (farmer → `403`, giao diện cũng **ẩn nút
> Xoá**). `GET /health`, `GET /batches`, `GET /batches/{id}` vẫn **công khai**;
> `GET /farms`, `POST /farms`, `PUT /farms/{id}`, `POST /batches`, `PUT /batches/{id}`,
> `POST /batches/{id}/split` yêu cầu đăng nhập (farmer hoặc admin).

### Phân quyền theo từng thao tác (Sprint 5)

| Thao tác | farmer | admin |
| --- | --- | --- |
| Xem dữ liệu (`GET /farms`, `GET /batches`) | ✅ | ✅ |
| Thêm dữ liệu (`POST /farms`, `POST /batches`) | ✅ | ✅ |
| Tách lô nông sản (`POST /batches/{id}/split`) | ✅ (lô được phép) | ✅ |
| Sửa dữ liệu (`PUT /farms/{id}`, `PUT /batches/{id}`) | ✅ | ✅ |
| Xoá dữ liệu (`DELETE /farms/{id}`, `DELETE /batches/{id}`) | ❌ `403 Forbidden` | ✅ |
| Quản lý tài khoản (`GET /users`) | ❌ `403 Forbidden` | ✅ |

### Xoá dữ liệu — cơ chế xoá dây chuyền (Sprint 5)

- **`DELETE /farms/{farm_id}`** xoá vùng trồng **và toàn bộ lô nông sản của nó**
  nhờ `cascade="all, delete-orphan"` khai báo ở quan hệ `Farm 1-N Batch`
  (`app/models.py`) → không để lại dữ liệu mồ côi. Số lô bị xoá kèm được trả về
  ở field `deleted_batches`.
- **`DELETE /batches/{batch_id}`** chỉ xoá lô đó → `deleted_batches` là `null`.
- Cả hai trả **`200 OK`** kèm `DeleteResponse` (không dùng `204 No Content` để
  giao diện hiển thị được thông báo cho người dùng):

```json
{
  "message": "Đã xoá vùng trồng #2 và 2 lô nông sản thuộc vùng đó.",
  "deleted_id": 2,
  "deleted_batches": 2
}
```

### Cấu trúc bảng `farms`

| Cột | Kiểu | Ràng buộc |
| --- | --- | --- |
| `id` | INTEGER | Khoá chính, tự tăng |
| `name` | VARCHAR(255) | Bắt buộc |
| `location` | VARCHAR(255) | Bắt buộc |
| `area` | FLOAT | Bắt buộc, **> 0** (đơn vị hecta) |
| `owner` | VARCHAR(255) | Bắt buộc |

> Bảng `farms` **không cần tạo bằng tay**: `init_db()` trong `lifespan` gọi
> `Base.metadata.create_all()` mỗi lần server khởi động, nên bảng mới sẽ tự
> được tạo nếu chưa tồn tại (không làm mất dữ liệu các bảng đã có).

### Cấu trúc bảng `batches` (lô nông sản - bổ sung T-19, T-39, T-49 & T-54)

| Cột | Kiểu | Ràng buộc | Mô tả |
| --- | --- | --- | --- |
| `id` | INTEGER | Khoá chính, tự tăng | Mã định danh lô hàng |
| `batch_code` | VARCHAR(100) | Cho phép NULL, có index | Mã lô nghiệp vụ chuẩn T-19 (ví dụ: `LOT-01-P11.01-20260925`) |
| `farm_id` | INTEGER | **Khoá ngoại → `farms.id`**, bắt buộc, có index | Thửa đất / vùng trồng liên kết |
| `product_name` | VARCHAR(255) | Bắt buộc | Tên nông sản / cây trồng |
| `quantity` | FLOAT | Bắt buộc, **> 0** (đơn vị kg) | Sản lượng thu hoạch |
| `harvest_date` | DATE | Bắt buộc, định dạng `yyyy-MM-dd` | Ngày thu hoạch ghi nhận |
| `parent_id` | INTEGER | **Khoá ngoại → `batches.id`**, NULL | Lô cha trực tiếp trong phả hệ (T-39, T-49) |
| `is_restricted` | BOOLEAN | Mặc định `False` | Bảo vệ quyền xem riêng tư theo T-54 |
| `owner` | VARCHAR(50) | Mặc định `"farmer"` | Chủ sở hữu lô nông sản |

**Quan hệ:**
- `Farm 1 ---- N Batch`: Một vùng trồng sở hữu nhiều lô nông sản thu hoạch.
- `Batch (parent) 1 ---- N Batch (children)`: Phả hệ tự tham chiếu (Self-referential) phục vụ tách lô T-39 và phân tầng truy vết nguồn gốc T-49.
- `Batch 1 ---- N BatchEvent`: Một lô nông sản sở hữu chuỗi sự kiện được băm mật mã SHA-256 (T-29 / SCRUM-45).

### Cấu trúc bảng `batch_events` (sự kiện chuỗi cung ứng chống sửa lén - T-29)

| Cột | Kiểu | Ràng buộc | Mô tả |
| --- | --- | --- | --- |
| `id` | INTEGER | Khoá chính, tự tăng | Mã định danh bản ghi sự kiện |
| `batch_id` | INTEGER | **Khoá ngoại → `batches.id`**, bắt buộc, có index | Lô nông sản liên kết |
| `sequence` | INTEGER | Bắt buộc | Số thứ tự bước trong chuỗi cung ứng (1, 2, 3...) |
| `event_type` | VARCHAR(100) | Bắt buộc | Loại sự kiện (`HARVEST`, `COLD_STORAGE_IN`, `TRANSPORT_DISPATCH`...) |
| `data` | VARCHAR(1000) | Bắt buộc | Dữ liệu chi tiết sự kiện (dạng JSON / telemetry chuỗi lạnh) |
| `timestamp` | VARCHAR(50) | Bắt buộc | Thời điểm ghi nhận sự kiện (ISO-8601) |
| `prev_hash` | VARCHAR(64) | Bắt buộc | Mã băm SHA-256 của sự kiện liền trước (`0*64` nếu là genesis) |
| `hash` | VARCHAR(64) | Bắt buộc, có index | Mã băm SHA-256 xác thực toàn vẹn của sự kiện hiện tại |

---

### Chức năng T-49 & T-54: Truy vết nguồn gốc, Lô gốc & Phân quyền bảo mật

#### 1. Endpoint Truy Vết Nguồn Gốc T-49
`GET /batches/{batch_id}/trace` (hoặc các alias `/lineage`, `/origin`)

- **Xác thực:** HTTP Basic (hoặc công khai nếu lô không bị bảo vệ T-54).
- **Phân quyền T-54:**
  - Nếu `is_restricted == True`: Chỉ **Admin** hoặc **Chủ sở hữu (`owner`)** mới được phép xem; người dùng khác bị trả về mã lỗi **`403 Forbidden`**.
  - Nếu `is_restricted == False`: Cho phép truy vết bình thường.
- **Cache 60 giây:**
  - Kết quả truy vết phả hệ được cache in-memory theo `batch_id` trong **60 giây**.
  - Tự động xoá cache khi lô được cập nhật (`PUT /batches/{id}`) hoặc xoá (`DELETE /batches/{id}`).
  - Kiểm tra quyền xem T-54 luôn chạy **trước khi đọc cache** để đảm bảo bảo mật.
- **Dữ liệu trả về (`BatchTraceResponse`):**
  - `root_batch`: Thông tin lô gốc thu hoạch đầu tiên (F0).
  - `origin_farm`: Thông tin vùng trồng / thửa đất của lô gốc (tên vùng trồng, vị trí địa lý, diện tích canh tác, chủ sở hữu / hợp tác xã).
  - `lineage`: Danh sách phân tầng phả hệ từ Tầng 1 (Lô gốc F0) qua các tầng trung gian đến Lô hiện tại.
  - `cached`: `true` nếu lấy từ cache, `false` nếu tính toán mới.
  - `cache_remaining_seconds`: Thời gian sống còn lại của cache (0 - 60s).

Ví dụ Response `200 OK`:
```json
{
  "batch_id": 2,
  "product_name": "Xoài Cát Chu Xuất Khẩu Sang Nhật",
  "quantity": 2200.0,
  "harvest_date": "2026-09-28",
  "farm_id": 1,
  "farm_name": "Vùng Trồng Xoài Cát Chu Cao Lãnh (Thửa Đất #1)",
  "root_batch": {
    "id": 1,
    "farm_id": 1,
    "product_name": "Xoài Cát Chu Loại 1 (VietGAP)",
    "quantity": 1500.0,
    "harvest_date": "2026-09-25"
  },
  "origin_farm": {
    "id": 1,
    "name": "Vùng Trồng Xoài Cát Chu Cao Lãnh (Thửa Đất #1)",
    "location": "Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp",
    "area": 4.5,
    "owner": "Hợp tác xã Xoài Mỹ Xương"
  },
  "lineage": [
    {
      "level": 1,
      "tier_name": "Tầng 1 (Lô gốc)",
      "batch_id": 1,
      "product_name": "Xoài Cát Chu Loại 1 (VietGAP)",
      "quantity": 1500.0,
      "harvest_date": "2026-09-25",
      "farm_id": 1,
      "farm_name": "Vùng Trồng Xoài Cát Chu Cao Lãnh (Thửa Đất #1)",
      "parent_id": null,
      "is_root": true,
      "is_current": false
    },
    {
      "level": 2,
      "tier_name": "Tầng 2 (Lô hiện tại)",
      "batch_id": 2,
      "product_name": "Xoài Cát Chu Xuất Khẩu Sang Nhật",
      "quantity": 2200.0,
      "harvest_date": "2026-09-28",
      "farm_id": 1,
      "farm_name": "Vùng Trồng Xoài Cát Chu Cao Lãnh (Thửa Đất #1)",
      "parent_id": 1,
      "is_root": false,
      "is_current": true
    }
  ],
  "cached": true,
  "cache_ttl_seconds": 60,
  "cache_remaining_seconds": 52
}
```

Ví dụ Response `403 Forbidden` (khi tài khoản `farmer` truy cập lô `#10` có `is_restricted=True`):
```json
{
  "detail": "Tài khoản 'farmer' không có quyền xem lô nông sản #10 theo chính sách T-54 (403 Forbidden)."
}
```

---

### Chức năng T-19, T-39 & S-17: Tách Lô Nông Sản Transactional (Batch Splitting)

Hàm tách lô (`split_batch` trong `app/batch_split.py`) cho phép chia tách một lô mẹ thành nhiều lô con theo danh sách khối lượng.

#### 1. Ràng buộc kỹ thuật & Kế thừa nguồn gốc (T-39 / SCRUM-55)
- **Kế thừa 100% thuộc tính nguồn gốc:** Lô con kế thừa chính xác `product_name`, `farm_id`, `harvest_date` từ lô mẹ; không cho phép client nhập sai lệch.
- **Quan hệ phả hệ (T-39):** Gán `parent_id = parent.id` để thiết lập cây phả hệ phục vụ truy vết phân tầng T-49.
- **Cập nhật khối lượng mẹ:** Trừ khối lượng còn lại của lô mẹ chính xác bằng tổng khối lượng các lô con (`parent.quantity -= sum(child_quantities)`). Nếu tách hết, khối lượng mẹ về `0.0`.
- **Đồng bộ Cache:** Tự động xoá cache truy vết T-49 của lô mẹ để dữ liệu mới nhất được cập nhật tức thì.

#### 2. Quy chuẩn sinh mã lô T-19 (`generate_batch_code`)
- **Lô con sau khi tách:** `LOT-{farm_id:02d}-P{parent_id}.{sequence:02d}-{YYYYMMDD}`
  - Ví dụ: `LOT-01-P11.01-20260925`, `LOT-01-P11.02-20260925`
- **Lô gốc (F0):** `LOT-{farm_id:02d}-{YYYYMMDD}-{sequence:02d}`
  - Ví dụ: `LOT-01-20260925-01`

#### 3. Cơ chế Atomic Transaction & Rollback sạch sẽ
Toàn bộ quy trình tách lô được bọc trong một Database Transaction duy nhất:
```python
try:
    # 1. Trừ khối lượng khả dụng của lô mẹ
    parent.quantity -= total_split_quantity
    
    # 2. Tạo từng lô con theo mã T-19 & kế thừa thuộc tính
    for idx, qty in enumerate(child_quantities, start=1):
        # nếu phát sinh lỗi tại bất kỳ lô con nào (kể cả lô thứ hai)...
        child = Batch(...)
        db.add(child)
        
    db.commit()
except Exception as e:
    db.rollback()  # Rollback sạch sẽ toàn bộ trạng thái DB!
    raise e
```
Nếu có lỗi phát sinh ở **lô con thứ hai** (hoặc bất kỳ lô nào), toàn bộ giao dịch được **rollback sạch sẽ 100%**: khối lượng của lô mẹ được phục hồi nguyên vẹn ban đầu và **không có bất kỳ lô con mồ côi nào** tồn tại trong cơ sở dữ liệu.

#### 4. Tiêu chí nghiệm thu S-17 (DoD / AC)
Bộ kiểm thử tự động tại `backend/test_split_s17.py` đã nghiệm thu toàn bộ:
1. **Ca 1 (Tách một phần):** Lô mẹ 1000 kg tách `[300, 400]` kg ➔ Lô mẹ còn 300 kg, 2 lô con tạo thành công mang mã T-19 kế thừa 100% nguồn gốc.
2. **Ca 2 (Tách toàn bộ):** Lô mẹ 500 kg tách `[200, 300]` kg ➔ Lô mẹ còn 0 kg.
3. **Ca 3 (Kiểm tra biên & validation):** Chặn tách vượt khối lượng mẹ (550 kg > 500 kg), chặn số cân âm/bằng 0, chặn mảng rỗng.
4. **Test Rollback:** Giả lập ném lỗi tại lô con thứ hai (`error_at_child_index=2`) ➔ Lô mẹ giữ nguyên 1000 kg, 0 lô con được ghi vào DB.

#### 5. API Endpoint `POST /batches/{batch_id}/split`
- **Quyền:** `farmer` hoặc `admin` (kiểm tra quyền sở hữu T-54).
- **Request Body (`BatchSplitRequest`):**
```json
{
  "child_quantities": [300.0, 400.0]
}
```
- **Response `201 Created` (`BatchSplitResponse`):**
```json
{
  "message": "Đã tách thành công 2 lô con từ lô mẹ #11.",
  "parent_batch": {
    "id": 11,
    "batch_code": "LOT-01-20260925-01",
    "farm_id": 1,
    "product_name": "Xoài Cát Chu VietGAP Thượng Hạng",
    "quantity": 300.0,
    "harvest_date": "2026-09-25",
    "parent_id": null
  },
  "child_batches": [
    {
      "id": 12,
      "batch_code": "LOT-01-P11.01-20260925",
      "farm_id": 1,
      "product_name": "Xoài Cát Chu VietGAP Thượng Hạng",
      "quantity": 300.0,
      "harvest_date": "2026-09-25",
      "parent_id": 11
    },
    {
      "id": 13,
      "batch_code": "LOT-01-P11.02-20260925",
      "farm_id": 1,
      "product_name": "Xoài Cát Chu VietGAP Thượng Hạng",
      "quantity": 400.0,
      "harvest_date": "2026-09-25",
      "parent_id": 11
    }
  ],
  "total_split_quantity": 700.0,
  "remaining_parent_quantity": 300.0
}
```

---

### Chức năng T-29 (SCRUM-45): Cơ chế Chống Sửa Lén Bản Ghi Chuỗi Sự Kiện (Cryptographic Hash Chain)

Theo định hướng từ nghiên cứu **SCRUM-9 (K-01)**, hệ thống không dùng Blockchain phức tạp và tốn kém tài nguyên, mà áp dụng giải pháp **Cryptographic Hash Chain (Chuỗi băm mật mã SHA-256)** gọn nhẹ, lưu trữ trực tiếp trong bảng cơ sở dữ liệu `batch_events`.

#### 1. Nguyên lý hoạt động
Mỗi sự kiện trong chuỗi cung ứng nông sản (thu hoạch, kiểm định, rửa/sơ chế, đóng gói, lưu kho lạnh, vận chuyển...) được lưu thành một bản ghi có hai trường mã băm:
- **`prev_hash`**: Mã băm SHA-256 của sự kiện liền kề trước đó. Bản ghi đầu tiên có `prev_hash = "0" * 64` (Genesis Hash).
- **`hash`**: Mã băm SHA-256 tính từ toàn bộ nội dung của bản ghi hiện tại và `prev_hash`:
  ```python
  payload = f"{batch_id}|{sequence}|{event_type}|{data}|{timestamp}|{prev_hash}"
  hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
  ```

#### 2. Cơ chế kiểm tra tính toàn vẹn 2 lớp (`verify_batch_events_integrity`)
Khi quét chuỗi sự kiện của một lô nông sản, hàm thực hiện kiểm tra 2 lớp:
1. **Lớp 1 — Toàn vẹn nội dung bản ghi (Content Integrity):**
   Hệ thống tính toán lại hash kỳ vọng từ các trường dữ liệu và so sánh với `event.hash` lưu trữ trong database. Nếu không khớp:
   ➔ Báo cờ **`DATA_MODIFIED`**: bản ghi đã bị can thiệp, sửa lén nội dung trực tiếp qua câu lệnh SQL (bypass ứng dụng).
2. **Lớp 2 — Liên kết chuỗi mật mã (Chain Continuity):**
   Đối chiếu `event.prev_hash` với `previous_event.hash` và kiểm tra `sequence` liên tục. Nếu không khớp:
   ➔ Báo cờ **`RECORD_DELETED_OR_CHAIN_BROKEN`**: phát hiện có bản ghi ở giữa bị xoá thủ tiêu dấu vết hoặc thứ tự bị xáo trộn.

#### 3. Bằng chứng nghiệm thu 3 ca kiểm thử (DoD / AC)
Kịch bản dựng một lô có đúng 10 sự kiện liên tiếp của chuỗi cung ứng lạnh và lần lượt kiểm tra:

| Ca kiểm thử | Hành động giả lập | Kết quả phát hiện | Khẳng định nghiệm thu |
| :--- | :--- | :--- | :--- |
| **Ca 1 (Sửa lén bằng SQL)** | Chạy lệnh SQL `UPDATE batch_events SET data = '{"temp_c": 15.0}' WHERE id = 6` (sửa nhiệt độ từ 3.8°C thành 15°C) | `is_valid: False`<br>`tamper_type: DATA_MODIFIED`<br>`tampered_event_id: 6`<br>`recorded_hash != expected_hash` | **PASS 100%** — Bắt đúng bản ghi #6 bị sửa lén dữ liệu qua SQL trực tiếp |
| **Ca 2 (Xoá bằng SQL)** | Chạy lệnh SQL `DELETE FROM batch_events WHERE id = 5` (xoá bản ghi lưu kho lạnh) | `is_valid: False`<br>`tamper_type: RECORD_DELETED_OR_CHAIN_BROKEN`<br>`tampered_event_id: 6`<br>`recorded_prev_hash != expected_prev_hash` | **PASS 100%** — Bắt đúng vị trí đứt gãy tại bản ghi #6 khi bản ghi #5 bị xoá |
| **Ca 3 (Giữ nguyên vẹn)** | Giữ nguyên 10 sự kiện chuẩn không chỉnh sửa, không xoá | `is_valid: True`<br>`status: VERIFIED`<br>`tamper_type: None`<br>`verified_count: 10/10` | **PASS 100%** — Khẳng định 10/10 mắt xích toàn vẹn từ Genesis đến đích |

#### 4. Khả năng chạy lại tự động (Idempotency & CI Ready)
- Kịch bản tự động dọn dẹp sạch sẽ dữ liệu thử nghiệm sau mỗi ca kiểm thử, bảo đảm có thể chạy lại vô số lần mà **không cần dọn dẹp thủ công**.
- Hỗ trợ cả 2 phương thức chạy:
  - **Script độc lập:** `python backend/test_t29_tamper_proofing.py`
  - **Pytest trong CI:** `python -m pytest backend/tests/test_t29_tamper_proofing.py -v`

#### 5. API Endpoints
- **Ghi nhận sự kiện:** `POST /batches/{batch_id}/events`
- **Xem danh sách sự kiện:** `GET /batches/{batch_id}/events`
- **Kiểm tra toàn vẹn & chống sửa lén:** `GET /batches/{batch_id}/events/verify`

Ví dụ Response `GET /batches/1/events/verify`:
```json
{
  "batch_id": 1,
  "is_valid": true,
  "status": "VERIFIED",
  "total_events": 10,
  "verified_count": 10,
  "tamper_type": null,
  "tampered_event_id": null,
  "tampered_sequence": null,
  "detail": "Toàn bộ 10/10 sự kiện hoàn toàn toàn vẹn, chuỗi băm hợp lệ 100%."
}
```

---

### Cấu trúc bảng `users` (Sprint 4)

| Cột | Kiểu | Ràng buộc |
| --- | --- | --- |
| `id` | INTEGER | Khoá chính, tự tăng |
| `username` | VARCHAR(50) | Bắt buộc, **unique + index** |
| `password` | VARCHAR(64) | Bắt buộc, **SHA-256 hex** (không lưu mật khẩu gốc) |
| `role` | VARCHAR(20) | Bắt buộc, `admin` hoặc `farmer` |

**Tài khoản mặc định** do `seed_default_users()` trong `app/database.py` tạo ở lần
chạy đầu tiên và **không ghi đè** nếu tài khoản đã tồn tại:

| Tài khoản | Mật khẩu | Vai trò (`role`) | Quyền |
| --- | --- | --- | --- |
| `admin` | `123456` | `admin` | Toàn bộ chức năng, xem được `GET /users` |
| `farmer` | `123456` | `farmer` | Quản lý vùng trồng + lô nông sản (`/farms`, `POST /batches`) |

### Cơ chế đăng nhập & phân quyền (Sprint 4 - không dùng JWT)

- **Đăng nhập:** `POST /auth/login` nhận `{username, password}`, băm mật khẩu bằng
  `hashlib.sha256` rồi so sánh bằng `hmac.compare_digest` với hash trong bảng
  `users`; đúng thì trả `{username, role}`, sai thì `401` (**không** phân biệt
  "sai username" hay "sai mật khẩu" để tránh dò tài khoản).
- **Không có JWT/session:** response `/auth/login` chỉ trả `username` + `role`.
  Các API cần quyền nhận thông tin đăng nhập qua header
  **`Authorization: Basic base64(username:password)`** - dependency
  `get_current_user` (dùng `HTTPBasic`) giải mã và tra bảng `users` cho mọi request.
- **Hai mức quyền** (khai báo trong `app/security.py`):
  - `require_farmer` → cho phép **farmer và admin**: `GET /farms`, `POST /farms`, `POST /batches`;
  - `require_admin` → **chỉ admin**: `GET /users`.
- **Mã lỗi:** `401 Unauthorized` = thiếu/sai thông tin đăng nhập (hoặc header Basic
  sai định dạng); `403 Forbidden` = đã đăng nhập nhưng **không đủ vai trò**.
- **Swagger UI:** nhờ `HTTPBasic` khai báo trong security, `/docs` tự hiện nút
  **Authorize** (nhập `admin` / `123456` là gọi được mọi API).
- ⚠️ **Lưu ý bảo mật (đủ cho bài tập):** SHA-256 **không salt**, mật khẩu gửi lại ở
  mỗi request và chưa có hết hạn phiên - Sprint sau nên thay bằng `bcrypt` + JWT.

---

## 4. Hướng dẫn test API bằng Swagger

### 4.1. Test trực tiếp trên Swagger UI

1. Chạy server: `uvicorn app.main:app --reload`
2. Mở <http://127.0.0.1:8000/docs>
3. **Đăng nhập (Sprint 4):** bấm nút **Authorize** (biểu tượng ổ khoá ở góc phải
   trên) → nhập Username `admin`, Password `123456` → **Authorize** → **Close**.
   Từ đó mọi request gửi từ Swagger đều kèm header `Authorization: Basic ...`.
   *Chưa đăng nhập* thì `GET /farms`, `POST /farms`, `POST /batches` trả **`401`**.
4. **Tạo vùng trồng:** mở `POST /farms` → bấm **Try it out** → dán JSON body bên
   dưới vào ô *Request body* → bấm **Execute**
   → mong đợi **`201 Created`**, response body có thêm `id` do database sinh ra.
5. **Lấy danh sách:** mở `GET /farms` → **Try it out** → **Execute**
   → mong đợi **`200 OK`** và một mảng JSON (mảng rỗng `[]` nếu chưa có dữ liệu).
6. **Kiểm tra validate:** gửi lại `POST /farms` với `"area": -1` hoặc bỏ trống
   `name` → mong đợi **`422 Unprocessable Entity`** kèm mô tả lỗi.
7. **Tạo lô nông sản:** mở `POST /batches` → **Try it out** → dán JSON body thứ hai
   bên dưới (nhớ `farm_id` là id vùng trồng vừa tạo ở bước 4) → **Execute**
   → mong đợi **`201 Created`**.
8. **Danh sách lô:** mở `GET /batches` → **Try it out** → **Execute**
   → mong đợi **`200 OK`** và mảng các lô nông sản.
9. **Chi tiết một lô:** mở `GET /batches/{batch_id}` → **Try it out** → nhập `1`
   → **Execute** → mong đợi **`200 OK`**; thử nhập `999` → **`404 Not Found`**.
10. **Kiểm tra chặn dữ liệu sai của Batch:** gửi `POST /batches` với `"farm_id": 999`
    → mong đợi **`404`** kèm `"Không tìm thấy vùng trồng có id=999."`;
    gửi `"quantity": -1` hoặc để trống `product_name` → mong đợi **`422`**.
11. **Kiểm tra `/health` vẫn hoạt động:** mở `GET /health` → **Execute**
    → mong đợi `200` + `{"status": "running"}`.

**Kiểm tra phân quyền (Sprint 4):**

12. Mở `POST /auth/login` → **Try it out** → body
    `{"username": "admin", "password": "123456"}` → **Execute**
    → mong đợi **`200`** + `{"username": "admin", "role": "admin"}`;
    thử lại với `"password": "sai"` → mong đợi **`401`**.
13. `GET /users` **khi đang Authorize bằng `admin`** → **`200`** (danh sách 2 tài
    khoản, **không** có trường `password`). Bấm **Authorize** →
    **Logout** rồi đăng nhập lại bằng `farmer` / `123456` → gọi `GET /users`
    → mong đợi **`403 Forbidden`** (`"Chỉ tài khoản admin được phép..."`),
    còn `GET /farms` với `farmer` → **`200`** (farmer vẫn quản lý được nông sản).

**Kiểm tra CRUD mới (Sprint 5)** — bấm **Authorize** lại bằng `admin` / `123456`:

14. **Sửa vùng trồng:** `PUT /farms/{farm_id}` → **Try it out** → nhập `farm_id` = `1`
    → dán JSON body vùng trồng (đổi `name`/`area` tuỳ ý) → **Execute**
    → mong đợi **`200 OK`** với dữ liệu mới. Thử `farm_id` = `999` → **`404`**;
    xoá bớt 1 trường trong body → **`422`**.
15. **Sửa lô nông sản:** `PUT /batches/{batch_id}` → **Try it out** → `batch_id` = `1`
    → đổi `product_name`/`quantity` → **`200 OK`**. Đổi `farm_id` sang id không
    tồn tại → **`404`** (`"Không tìm thấy vùng trồng..."`).
16. **Phân quyền xoá:** đang Authorize bằng `admin` → **Logout** → Authorize bằng
    `farmer` / `123456` → `DELETE /batches/{batch_id}` → mong đợi **`403 Forbidden`**.
17. **Xoá lô nông sản (admin):** Authorize lại bằng `admin` → **Try it out** →
    `batch_id` của một lô vừa tạo → **Execute** → mong đợi **`200 OK`** + body
    `{"message": "...", "deleted_id": <id>, "deleted_batches": null}`;
    gọi lại lần 2 với cùng id → **`404`**.
18. **Xoá vùng trồng (admin, xoá dây chuyền):** tạo 1 vùng trồng mới → tạo 1-2 lô
    cho vùng đó → `DELETE /farms/{farm_id}` → **`200 OK`** với `deleted_batches`
    đúng bằng số lô vừa tạo → kiểm tra `GET /batches` → các lô đó **đã biến mất**.

JSON body mẫu để dán vào Swagger:

```json
{
  "name": "Vùng trồng xoài Cao Lãnh",
  "location": "Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp",
  "area": 2.5,
  "owner": "Hợp tác xã Xoài Mỹ Xương"
}
```

```json
{
  "farm_id": 1,
  "product_name": "Xoài cát Chu",
  "quantity": 120.5,
  "harvest_date": "2026-01-15"
}
```

### 4.2. Test bằng PowerShell khi server đang chạy

**Cách 1 — `Invoke-RestMethod` (khuyến nghị, không gặp lỗi escaping):**

```powershell
# 1. Kiểm tra hệ thống (công khai, không cần đăng nhập)
Invoke-RestMethod http://127.0.0.1:8000/health

# 2. Đăng nhập -> nhận username + role (không có token vì KHÔNG dùng JWT)
$login = '{"username":"admin","password":"123456"}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/auth/login -Method Post -ContentType 'application/json; charset=utf-8' -Body $login

# 3. Tạo header xác thực HTTP Basic để tái sử dụng cho các request sau
$token = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('admin:123456'))
$auth = @{ Authorization = "Basic $token" }

# 4. Tạo vùng trồng mới (CẦN QUYỀN: thiếu -Headers $auth sẽ nhận 401)
$body = '{"name":"Vung trong xoai Cao Lanh","location":"Xa My Xuong, Cao Lanh, Dong Thap","area":2.5,"owner":"HTX Xoai My Xuong"}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/farms -Method Post -Headers $auth -ContentType 'application/json; charset=utf-8' -Body $body

# 5. Lấy danh sách vùng trồng (CẦN QUYỀN)
Invoke-RestMethod http://127.0.0.1:8000/farms -Headers $auth | ConvertTo-Json

# 6. Tạo lô nông sản cho vùng trồng id=1 (CẦN QUYỀN)
$batch = '{"farm_id":1,"product_name":"Xoai cat Chu","quantity":120.5,"harvest_date":"2026-01-15"}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/batches -Method Post -Headers $auth -ContentType 'application/json; charset=utf-8' -Body $batch

# 7. Danh sách lô + chi tiết lô id=1 (công khai, không cần header)
Invoke-RestMethod http://127.0.0.1:8000/batches | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/batches/1 | ConvertTo-Json

# 8. Phân quyền: /users chỉ admin xem được
Invoke-RestMethod http://127.0.0.1:8000/users -Headers $auth | ConvertTo-Json

# 9. farmer gọi /users -> 403 Forbidden (đã đăng nhập nhưng sai vai trò)
$farmerToken = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('farmer:123456'))
Invoke-RestMethod http://127.0.0.1:8000/users -Headers @{ Authorization = "Basic $farmerToken" }

# 10. Sửa vùng trồng id=1 (PUT) - farmer và admin đều được phép
$farmUpdate = '{"name":"Vung trong xoai Cao Lanh (da sua)","location":"Cao Lanh, Dong Thap","area":3.2,"owner":"HTX Xoai My Xuong"}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/farms/1 -Method Put -Headers $auth -ContentType 'application/json; charset=utf-8' -Body $farmUpdate

# 11. Sửa lô nông sản id=1 (PUT) - đổi tên sản phẩm + số lượng
$batchUpdate = '{"farm_id":1,"product_name":"Xoai cat Chu loai 1","quantity":150,"harvest_date":"2026-01-16"}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/batches/1 -Method Put -Headers $auth -ContentType 'application/json; charset=utf-8' -Body $batchUpdate

# 12. farmer xoá dữ liệu -> 403 Forbidden (chỉ admin được xoá)
try {
    Invoke-RestMethod -Uri http://127.0.0.1:8000/batches/2 -Method Delete -Headers @{ Authorization = "Basic $farmerToken" }
} catch {
    "farmer xoa -> HTTP $($_.Exception.Response.StatusCode.value__)"
}

# 13. admin xoá lô nông sản -> 200 OK + {message, deleted_id, deleted_batches}
Invoke-RestMethod -Uri http://127.0.0.1:8000/batches/2 -Method Delete -Headers $auth

# 14. admin xoá vùng trồng -> xoá kèm mọi lô của vùng đó (deleted_batches > 0)
Invoke-RestMethod -Uri http://127.0.0.1:8000/farms/2 -Method Delete -Headers $auth
```

**Cách 2 — `curl.exe` (bắt buộc dùng `--%`, xem lưu ý bên dưới):**

```powershell
# Công khai
curl.exe -s http://127.0.0.1:8000/health
curl.exe -s http://127.0.0.1:8000/batches
curl.exe -s http://127.0.0.1:8000/batches/1

# Đăng nhập (trả về username + role)
curl.exe --% -s -X POST http://127.0.0.1:8000/auth/login -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"123456\"}"

# API cần quyền: dùng -u (curl tự mã hoá Basic base64)
curl.exe -s -u admin:123456 http://127.0.0.1:8000/farms

curl.exe --% -s -X POST http://127.0.0.1:8000/farms -u admin:123456 -H "Content-Type: application/json" -d "{\"name\":\"Vung trong xoai Cao Lanh\",\"location\":\"Cao Lanh, Dong Thap\",\"area\":2.5,\"owner\":\"HTX Xoai\"}"

curl.exe --% -s -X POST http://127.0.0.1:8000/batches -u farmer:123456 -H "Content-Type: application/json" -d "{\"farm_id\":1,\"product_name\":\"Xoai cat Chu\",\"quantity\":120.5,\"harvest_date\":\"2026-01-15\"}"

# Quên -u -> 401 ; dùng tài khoản farmer cho /users -> 403
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:8000/farms
curl.exe -s -u farmer:123456 -o NUL -w "%{http_code}`n" http://127.0.0.1:8000/users

# Sửa dữ liệu (PUT): farmer cũng làm được
curl.exe --% -s -X PUT http://127.0.0.1:8000/farms/1 -u farmer:123456 -H "Content-Type: application/json" -d "{\"name\":\"Vung trong xoai (da sua)\",\"location\":\"Cao Lanh, Dong Thap\",\"area\":3.2,\"owner\":\"HTX Xoai\"}"

curl.exe --% -s -X PUT http://127.0.0.1:8000/batches/1 -u farmer:123456 -H "Content-Type: application/json" -d "{\"farm_id\":1,\"product_name\":\"Xoai cat Chu\",\"quantity\":150,\"harvest_date\":\"2026-01-16\"}"

# Xoá dữ liệu (DELETE): farmer -> 403, admin -> 200
curl.exe -s -u farmer:123456 -X DELETE -o NUL -w "%{http_code}`n" http://127.0.0.1:8000/batches/2
curl.exe -s -u admin:123456 -X DELETE http://127.0.0.1:8000/batches/2
curl.exe -s -u admin:123456 -X DELETE http://127.0.0.1:8000/farms/2
```

> ⚠️ **Lưu ý quan trọng (đã kiểm chứng thực tế):** trên **PowerShell 5.1**, cách viết
> `curl.exe -d '{"name":"..."}'` (nháy đơn) hoặc `-d "{\"name\":\"...\"}"` (không có `--%`)
> sẽ bị PowerShell làm hỏng dấu nháy → server trả **`422 JSON decode error`**.
> Hãy dùng `--%` (stop-parsing) hoặc `Invoke-RestMethod`.
>
> Với tên tiếng Việt **có dấu**, nên test bằng **Swagger UI** (xử lý UTF-8 chuẩn) thay vì
> dán trực tiếp trong terminal, để tránh lỗi hiển thị/encoding của PowerShell/CMD.
>
> Nếu chạy script Python in ra chữ tiếng Việt mà bị `UnicodeEncodeError: 'charmap'
> codec...` (thường gặp khi **pipe/redirect output** trên Windows), hãy đặt biến môi
> trường trước khi chạy: `$env:PYTHONIOENCODING='utf-8'`.

### 4.3. Xem dữ liệu đã lưu trong SQLite

Database là file `backend/ttcs.db` (tự sinh). Xem nhanh bằng Python:

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; print(sqlite3.connect('ttcs.db').execute('SELECT * FROM farms').fetchall())"
```

Hoặc mở file bằng DB Browser for SQLite. Muốn **reset dữ liệu**: tắt server, xoá
`ttcs.db`, chạy lại server (bảng sẽ được tạo mới, rỗng và `seed_default_users()`
**tạo lại 2 tài khoản demo** `admin`/`farmer` với mật khẩu `123456`).

Xem nhanh bảng tài khoản (cột `password` là hash SHA-256, không phải mật khẩu thô):

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; print(sqlite3.connect('ttcs.db').execute('SELECT id, username, role FROM users').fetchall())"
```

---

## 5. Giải thích từng file

| File | Vai trò |
| --- | --- |
| `app/main.py` | Entrypoint: tạo `FastAPI(...)`, cấu hình CORS, dùng `lifespan` để gọi `init_db()` khi server start, và `include_router` để gom các endpoint. Khi mở rộng, chỉ cần thêm 1 dòng `app.include_router(...)`. |
| `app/database.py` | Tầng hạ tầng dữ liệu: tạo `engine` kết nối SQLite (`check_same_thread=False` vì FastAPI có thể xử lý request trên thread khác — tham số này chỉ dành riêng cho SQLite), `SessionLocal` để mở session mỗi request, `Base` (DeclarativeBase) cho mọi model, `get_db()` (dependency đóng session tự động), `init_db()` (tạo bảng từ metadata) và `seed_default_users()` (tạo 2 tài khoản demo `admin`/`farmer` nếu chưa có). |
| `app/models.py` | Nơi khai báo bảng ORM (SQLAlchemy 2.0 style: `Mapped` + `mapped_column`). Hiện có: `Farm` → bảng `farms` (`id`, `name`, `location`, `area`, `owner`) và `Batch` → bảng `batches` (`id`, `farm_id` FK → `farms.id`, `product_name`, `quantity`, `harvest_date`) với quan hệ 2 chiều `Farm 1-N Batch` (`farm.batches` ↔ `batch.farm`), cùng `User` → bảng `users` (`id`, `username` unique, `password` = hash SHA-256, `role`) kèm hằng số `ROLE_ADMIN`/`ROLE_FARMER`. Thêm bảng mới ở đây thì `init_db()` sẽ tự tạo. |
| `app/schemas.py` | Pydantic models mô tả dữ liệu request/response: `HealthResponse`, `FarmCreate`/`FarmResponse`, `BatchCreate`/`BatchResponse` (`farm_id > 0`, chuỗi không rỗng, `quantity > 0`, `harvest_date` kiểu `date`). `*Response` dùng `from_attributes=True` để trả thẳng ORM object kèm `id`; Sprint 4 bổ sung `LoginRequest` (`username`, `password`), `LoginResponse` (`username`, `role`) và `UserResponse` (**không** có trường `password`); Sprint 5 bổ sung `FarmUpdate`/`BatchUpdate` (kế thừa `*Create` để dùng lại validate, phục vụ `PUT`) và `DeleteResponse` (`message`, `deleted_id`, `deleted_batches`). Tách khỏi `models.py` để không lộ cấu trúc bảng ra API. |
| `app/security.py` | **Sprint 4** — xác thực & phân quyền *không JWT*: `hash_password()` / `verify_password()` (SHA-256 + `hmac.compare_digest`, chỉ dùng thư viện chuẩn), `authenticate_user()` (tra bảng `users`), `basic_scheme = HTTPBasic(auto_error=False)` và 3 dependency: `get_current_user()` (**401** nếu thiếu/sai thông tin đăng nhập), `require_admin()` (**403** nếu không phải admin), `require_farmer()` (cho cả farmer và admin). Router chỉ cần thêm `user = Depends(require_admin)` là đã có phân quyền. |
| `app/routers/auth.py` | **Sprint 4** — router `Auth`: `POST /auth/login` kiểm tra `username`/`password` với bảng `users`, trả `{username, role}` (**200**); sai thì **401**. **Không sinh token** — client dùng lại thông tin đăng nhập qua header HTTP Basic cho các request sau (mục đích chính của endpoint này là để frontend biết vai trò). |
| `app/routers/users.py` | **Sprint 4** — router `Users`: `GET /users` trả danh sách tài khoản sắp theo `id` và **không kèm mật khẩu**. Dùng `Depends(require_admin)` nên: admin → **200**, farmer → **403**, chưa đăng nhập → **401**. |
| `app/routers/health.py` | Router chứa endpoint `GET /health`, khai báo `response_model=HealthResponse`, trả về `{"status": "running"}`. |
| `app/routers/farms.py` | Router module Farm - **CRUD đầy đủ**: `POST /farms` (thêm bản ghi, `commit` + `refresh`, rollback nếu lỗi DB), `GET /farms` (truy vấn bằng `select()` của SQLAlchemy 2.0), `PUT /farms/{farm_id}` (Sprint 5 - ghi đè từng trường bằng `setattr`, **404** nếu không thấy) và `DELETE /farms/{farm_id}` (Sprint 5 - **chỉ admin**, xoá kèm các lô nhờ cascade, trả `DeleteResponse`). |
| `app/routers/batches.py` | Router module Batch - **CRUD đầy đủ**: `POST /batches` (**404** nếu `farm_id` không tồn tại — kiểm tra bằng `db.get(Farm, ...)` trước khi ghi), `GET /batches` (danh sách, sắp theo `id`), `GET /batches/{batch_id}` (**404** nếu không thấy), `PUT /batches/{batch_id}` (Sprint 5 - kiểm tra lại `farm_id` mới trước khi ghi) và `DELETE /batches/{batch_id}` (Sprint 5 - **chỉ admin**). |
| `app/routers/__init__.py` | Gom và export các router con để `main.py` import ngắn gọn (`from app.routers import auth, batches, farms, health, users`). |
| `app/__init__.py` | Đánh dấu `app` là package Python; khai báo `__version__ = "0.2.0"` dùng cho metadata Swagger. |
| `requirements.txt` | Ghim phiên bản thư viện: `fastapi`, `uvicorn[standard]`, `SQLAlchemy`, `pydantic` — đảm bảo cả nhóm cài ra môi trường giống nhau. **Sprint 4 không thêm thư viện nào**: băm mật khẩu dùng `hashlib`/`hmac` có sẵn, xác thực dùng `fastapi.security.HTTPBasic` của FastAPI. |
| `.gitignore` | Bỏ qua `.venv/`, `__pycache__/`, `*.db`... để không commit rác và dữ liệu local. |

---

## 6. Hướng mở rộng ở Sprint sau

1. **Thêm bảng:** khai báo model mới trong `app/models.py` → bảng tự được tạo
   ở lần chạy tiếp theo.
2. **Thêm endpoint:** tạo file mới trong `app/routers/` (ví dụ `cold_chain.py`),
   thêm schema tương ứng vào `app/schemas.py`, rồi đăng ký router trong
   `app/main.py`.
3. **Bổ sung CRUD:** `GET /farms/{farm_id}` (trả 404 nếu không thấy); cho
   `GET /batches` hỗ trợ lọc theo vùng trồng (`?farm_id=1`) và phân trang
   (`skip`, `limit`). *(`PUT`/`DELETE` cho cả Farm và Batch đã hoàn thành ở Sprint 5.)*
4. **Trả kèm thông tin vùng trồng trong chi tiết lô:** thêm trường
   `farm: FarmResponse` vào schema chi tiết lô (dùng `joinedload` để tránh N+1).
5. **Index & ràng buộc:** thêm `unique=True, index=True` cho cột cần tra cứu
   `name` (vùng trồng) để tăng tốc truy vấn.
6. **Dùng database trong endpoint:**
   ```python
   from fastapi import Depends
   from sqlalchemy.orm import Session
   from app.database import get_db

   @router.get("/cold-chain")
   def list_logs(db: Session = Depends(get_db)):
       ...
   ```
7. **Migration:** khi schema thay đổi nhiều, bổ sung Alembic thay vì
   `create_all()`.
8. **Cấu hình theo môi trường:** chuyển `DATABASE_URL`, CORS origin... sang biến
   môi trường (`.env` + `pydantic-settings`).
9. **Kiểm thử:** thêm `tests/` với `pytest` + `fastapi.testclient` (cần `httpx`).
10. **Bảo mật nâng cao (bảo vệ mật khẩu):** thay SHA-256 không salt bằng
    `bcrypt`/`argon2` (qua `passlib`), thêm chức năng **đổi mật khẩu**, khoá tài
    khoản khi đăng nhập sai nhiều lần và bắt buộc HTTPS khi triển khai (HTTP Basic
    gửi mật khẩu ở mỗi request).
11. **Thay HTTP Basic bằng JWT/session:** phát hành access token có thời hạn
    (+ refresh token hoặc session cookie) để client không phải lưu và gửi lại mật
    khẩu; thêm `POST /auth/logout`, `GET /auth/me`.
12. **Phân quyền chi tiết hơn:** thêm vai trò `inspector`/`retailer`, thêm cột
    `owner_id` (FK → `users.id`) cho `farms` để **farmer chỉ sửa được vùng trồng
    của mình** (row-level permission), ghi log ai đã tạo/sửa dữ liệu (audit trail).
13. **Xoá an toàn hơn:** chuyển sang **soft delete** (cột `deleted_at`) + endpoint
    khôi phục thay cho xoá cứng, yêu cầu xác nhận (`?force=true`) khi xoá vùng
    trồng còn lô nông sản, ghi log ai đã xoá bản ghi nào.

---

## 7. Lịch sử thay đổi

| Giai đoạn | Nội dung |
| --- | --- |
| Sprint 1 | Khung dự án: FastAPI + SQLite + SQLAlchemy, endpoint `GET /health`. |
| Sprint 2 | Module **Farm** (quản lý vùng trồng): model `Farm` → bảng `farms` (tự tạo), schemas `FarmCreate`/`FarmResponse`, router `app/routers/farms.py` với `POST /farms` (**201**) và `GET /farms` (**200**). `GET /health` giữ nguyên. |
| Sprint 3 | Module **Batch** (quản lý lô nông sản): model `Batch` → bảng `batches` (FK `farm_id` → `farms.id`, quan hệ `Farm 1 ---- N Batch`), schemas `BatchCreate`/`BatchResponse`, router `app/routers/batches.py` với `POST /batches` (**201**, trả **404** nếu `farm_id` không tồn tại), `GET /batches` (**200**) và `GET /batches/{batch_id}` (**200**/**404**). `GET /health`, `POST /farms`, `GET /farms` giữ nguyên. |
| Sprint 4 | **Đăng nhập + phân quyền cơ bản (không JWT):** model `User` → bảng `users` (`username` unique, mật khẩu băm SHA-256, `role`), `seed_default_users()` tạo sẵn `admin`/`farmer` (mật khẩu `123456`); module `app/security.py` với `hash_password`/`verify_password`/`authenticate_user` và dependency `get_current_user` (**401**), `require_admin` (**403**), `require_farmer`; router `POST /auth/login` (**200**/**401**) và `GET /users` (**200**, chỉ admin); áp `require_farmer` cho `GET /farms`, `POST /farms`, `POST /batches`. Cơ chế xác thực là **HTTP Basic** (Swagger có nút **Authorize**), không token/refresh token. |
| Sprint 5 | **Hoàn thiện CRUD + phân quyền xoá:** thêm `PUT /farms/{farm_id}` (**200**/**404**/**422**), `DELETE /farms/{farm_id}` (**200**, **chỉ admin**, xoá kèm mọi lô của vùng nhờ `cascade="all, delete-orphan"`), `PUT /batches/{batch_id}` (**200**, **404** nếu lô hoặc `farm_id` mới không tồn tại), `DELETE /batches/{batch_id}` (**200**, **chỉ admin**); schemas `FarmUpdate`/`BatchUpdate` (kế thừa `*Create`) và `DeleteResponse` (`message`, `deleted_id`, `deleted_batches`); `require_admin` áp cho cả 2 endpoint `DELETE`. Frontend: sửa lỗi `[hidden]` bị `display` đè (trước đây dashboard vẫn hiện khi chưa đăng nhập), ẩn toàn bộ dashboard/form khi chưa login, cột **Thao tác** (Sửa cho farmer + admin, Xoá **chỉ admin**), form dùng chung cho thêm/sửa (PUT khi đang sửa) và dashboard 3 thẻ (tổng vùng trồng, tổng lô nông sản, tổng sản lượng kg). |
| Task T-49 & T-54 | **Truy vết nguồn gốc & Phân quyền riêng tư:** Endpoint `GET /batches/{id}/trace` trả về kết quả truy vết nguồn gốc đa tầng (`lineage`), thông tin lô gốc (`root_batch`) và thông tin vùng trồng của lô gốc (`origin_farm`). Kiểm tra quyền xem riêng tư theo T-54 (trả `403 Forbidden` nếu không có quyền). In-memory cache 60 giây thread-safe kèm cơ chế tự động xoá cache khi cập nhật dữ liệu. |
| Task T-19, T-39 & S-17 | **Hàm tách lô nông sản Transactional:** Hàm `split_batch` và API `POST /batches/{id}/split`. Sinh mã lô tự động theo chuẩn T-19 (`LOT-{farm_id:02d}-P{parent_id}.{sequence:02d}-{YYYYMMDD}`). Ghi quan hệ phả hệ T-39 (`parent_id`), trừ khối lượng khả dụng của lô mẹ. Lô con kế thừa 100% thuộc tính nguồn gốc (`product_name`, `farm_id`, `harvest_date`). Vượt qua 3 ca kiểm thử của S-17 và bảo đảm rollback sạch sẽ nếu có lỗi ở lô con thứ hai. |
| Task T-29 (SCRUM-45) | **Cơ chế Chống Sửa Lén Bản Ghi (Cryptographic Hash Chain):** Module `app/event_chain.py` và model `BatchEvent`. Mỗi sự kiện lưu `prev_hash` và `hash` SHA-256. Kiểm tra toàn vẹn 2 tầng: bắt 100% hành vi sửa lén nội dung qua SQL (`DATA_MODIFIED`) và xoá bản ghi làm đứt chuỗi (`RECORD_DELETED_OR_CHAIN_BROKEN`). Vượt qua trọn vẹn cả 3 ca kiểm thử trong CI (`backend/tests/test_t29_tamper_proofing.py`), tự động dọn dẹp sạch sẽ không cần can thiệp thủ công. |


