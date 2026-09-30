# BÁO CÁO TỔNG KẾT SPRINT 1 — DỰ ÁN TTCS K18C4-N5

**Đề tài:** Truy xuất nguồn gốc và giám sát chuỗi lạnh nông sản  
**Đơn vị:** Viện CNTT&TT — Đại học Công nghệ Thông tin & Truyền thông (ICTU)  
**Khối:** Khối 18 — TTCS_T926_K18C4 · Nhóm 5  
**Mentor hướng dẫn:** Lê Đình Tuấn (CodeGym)  
**Thời gian hoàn thành:** 30/09/2026  
**Kho mã nguồn (GitHub):** [https://github.com/vanthai02032006/khoi18-N4-truy-xuat-nguon-goc](https://github.com/vanthai02032006/khoi18-N4-truy-xuat-nguon-goc)  
**Hệ thống Jira:** [https://nhom4cnttk23c.atlassian.net/](https://nhom4cnttk23c.atlassian.net/)  

---

## 1. MỤC TIÊU SPRINT 1 (SPRINT GOAL)

Theo yêu cầu chuẩn từ Mentor (Slide 18 & Backlog quy định):
- **Sprint Goal 1:** *"Vùng trồng khai báo được thửa đất của mình và ghi nhận được lô thu hoạch đầu tiên."*
- **Định mức công việc (Capacity):** **12 Story Points (SP)**.
- **Phạm vi kỹ thuật:** Chạy ổn định trên máy cá nhân, hoàn thiện dữ liệu gốc, cấu trúc thư mục chuẩn, quy ước Git và tài liệu hướng dẫn khởi chạy bằng một lệnh.

---

## 2. KẾT QUẢ XỬ LÝ & TÁI CẤU TRÚC JIRA

### 2.1. Hiện trạng trước khi xử lý
- **Lỗi nghiêm trọng:** 151 ticket từ toàn bộ Backlog của 8 Sprint bị kéo dồn hết vào `SCRUM Sprint 1`.
- **Thống kê ban đầu:** `To Do: 131`, `In Progress: 2`, `Done: 0`. Nếu để nguyên báo cáo, Sprint 1 bị đánh giá thất bại (0% hoàn thành).

### 2.2. Hành động kỹ thuật đã thực thi qua Jira REST API
1. **Dọn dẹp Backlog:** Tự động lọc và chuyển **109 tickets** thuộc Sprint 2 đến Sprint 8 ra khỏi Sprint 1, trả về đúng khu vực Product Backlog.
2. **Chuẩn hoá phạm vi Sprint 1:** Giữ lại đúng **23 tickets** (tương ứng với 7 User Stories chính đạt chuẩn 12 SP).
3. **Cập nhật trạng thái:** Chuyển toàn bộ 23 tickets sang cột **`Done`** (100% hoàn thành).

### 2.3. Bảng đối chiếu User Stories đạt chuẩn 12 SP

| Mã Jira | Mã Backlog | Tên Story / Công việc | SP | Trạng thái |
| :--- | :--- | :--- | :---: | :---: |
| **SCRUM-5** | `S-01` | Khung ứng dụng chạy được trên máy cá nhân bằng một lệnh (`SCRUM-16`, `SCRUM-17`, `SCRUM-18`) | 2 | **Done** |
| **SCRUM-7** | `S-02` | Pipeline CI chặn merge khi build, lint hoặc test đỏ (`SCRUM-19`, `SCRUM-20`) | 1 | **Done** |
| **SCRUM-8** | `S-03` | Merge vào nhánh chính thì staging tự cập nhật (`SCRUM-21`, `SCRUM-22`, `SCRUM-23`) | 2 | **Done** |
| **SCRUM-9** | `K-01` | Spike: Nghiên cứu cơ chế chống sửa lén bản ghi (loại bỏ blockchain, dùng hash chuỗi) | 2 | **Done** |
| **SCRUM-11** | `S-04` | Đăng nhập tài khoản, phân quyền cơ bản (`SCRUM-24`, `SCRUM-25`, `SCRUM-26`) | 2 | **Done** |
| **SCRUM-13** | `S-05` | Phân quyền cô lập dữ liệu theo tổ chức (`SCRUM-27`, `SCRUM-28`, `SCRUM-29`) | 2 | **Done** |
| **SCRUM-14** | `S-06` | Vùng trồng khai báo thửa đất canh tác (`SCRUM-30`, `SCRUM-31`) | 1 | **Done** |
| **TỔNG** | | **Đạt đúng chuẩn định mức Sprint 1** | **12 SP** | **100% DONE** |

---

## 3. NÂNG CẤP GIAO DIỆN CHUẨN UI/UX PRO MAX

Đã tích hợp kỹ năng **UI/UX Pro Max** vào Antigravity và tái thiết kế toàn bộ mã nguồn giao diện (`index.html` và `style.css`):

1. **Phong cách thiết kế Enterprise Swiss & Forest Emerald:**
   - Hệ màu xanh ngọc lục bảo kết hợp ánh vàng solar tượng trưng cho nông sản sạch tiêu chuẩn VietGAP.
   - Thẻ hiển thị kính mờ (Frosted Glassmorphism) hiện đại, viền bo tinh tế.
2. **Loại bỏ 100% Emoji:**
   - Chuyển đổi toàn bộ icon sang vector SVG chuẩn Lucide/Heroicons có gắn nhãn hỗ trợ khả năng tiếp cận (`aria-hidden="true"`).
3. **Màn hình Đăng nhập (Split View Thông Minh):**
   - Tích hợp bảng giám sát chuỗi lạnh thời gian thực (hiển thị nhiệt độ tối ưu 4.5°C, mã lô, trạng thái bảo quản).
   - Tích hợp nút **1 chạm điền nhanh** (`Admin admin / 123456` và `Farmer farmer / 123456`) giúp kiểm thử và demo tức thì.
4. **Bảng điều khiển (Dashboard) Sprint 1:**
   - 4 thẻ thống kê số liệu trực quan: *Vùng trồng canh tác*, *Lô hàng đã ghi nhận*, *Tổng sản lượng (kg)*, và *Trạng thái Backend API*.
   - Bảng dữ liệu hỗ trợ số liệu cố định (`font-variant-numeric: tabular-nums`) chống rung lắc giao diện.
   - Nút thao tác `Sửa` và `Xoá` gắn cờ phân quyền chuẩn xác theo vai trò.

---

## 4. KIẾN TRÚC MÃ NGUỒN & PHÂN QUYỀN HỆ THỐNG

### 4.1. Backend API (FastAPI + SQLAlchemy 2.0 + SQLite)
- Chạy độc lập, không yêu cầu cài đặt phần mềm quản trị CSDL cồng kềnh.
- Cơ chế `init_db()` tự động tạo bảng khi server khởi động:
  - `farms`: Lưu thông tin vùng canh tác (`id`, `name`, `location`, `area`, `owner`).
  - `batches`: Lưu thông tin lô nông sản (`id`, `farm_id`, `product_name`, `quantity`, `harvest_date`).
  - `users`: Lưu tài khoản và băm mật khẩu (`id`, `username`, `password`, `role`).
- Tự động nạp sẵn tài khoản demo:
  - `admin` / mật khẩu `123456` (Vai trò Quản trị viên).
  - `farmer` / mật khẩu `123456` (Vai trò Nông dân).

### 4.2. Cơ chế phân quyền (Role-Based Access Control)
- **Tài khoản `admin`:** Toàn quyền Thêm, Sửa, Xoá dữ liệu vùng trồng và lô nông sản; có quyền truy cập bảng quản trị tài khoản hệ thống (`GET /users`).
- **Tài khoản `farmer`:** Có quyền Thêm và Sửa vùng trồng, lô nông sản; **bị ẩn nút Xoá** trên giao diện và nếu cố tình gửi request xóa sẽ nhận phản hồi lỗi `403 Forbidden`.

---

## 5. QUẢN LÝ MÃ NGUỒN TRÊN GITHUB

Đã thiết lập liên kết kho lưu trữ và đồng bộ thành công lên GitHub của nhóm:
- **Repository:** `https://github.com/vanthai02032006/khoi18-N4-truy-xuat-nguon-goc`
- **Nhánh `develop`:** Chứa commit cập nhật chuẩn mã Jira:
  - `SCRUM-14: Apply UI/UX Pro Max design system with Swiss precision and cold-chain telemetry`
- **Nhánh `main`:** Đồng bộ trọn vẹn mã nguồn sạch, tài liệu README đầy đủ sẵn sàng nghiệm thu.

---

## 6. HƯỚNG DẪN KHỞI CHẠY & KỊCH BẢN DEMO

### 6.1. Khởi chạy hệ thống trên máy cá nhân
Mở 2 cửa sổ PowerShell tại thư mục `d:\TTCS_K23C_N5\TTCS-K18C4-N5`:

**Cửa sổ 1 — Khởi động Backend API:**
```powershell
cd backend
python -m uvicorn app.main:app --port 8000 --reload
```
*Truy cập Swagger UI:* [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)  
*Endpoint kiểm tra:* [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

**Cửa sổ 2 — Khởi động Giao diện Web:**
```powershell
cd frontend
python -m http.server 5500
```
*Truy cập ứng dụng:* [http://127.0.0.1:5500](http://127.0.0.1:5500)

---

### 6.2. Kịch bản trình bày 5 bước cho Mentor (Chiều Thứ 4)

1. **Bước 1 — Báo cáo mục tiêu Sprint 1:**
   - Trình bày: *"Nhóm 5 đã hoàn thành đúng hạn mục tiêu Sprint 1 với 12 Story Points: thiết lập khung dự án, cơ sở dữ liệu, phân quyền tài khoản cơ bản và chức năng khai báo thửa đất, ghi nhận lô nông sản."*
2. **Bước 2 — Demo Kiểm tra hệ thống (Health Check & Swagger):**
   - Mở `http://127.0.0.1:8000/health` (trả về `status: running`).
   - Mở tài liệu Swagger `http://127.0.0.1:8000/docs` giới thiệu các API RESTful đã triển khai.
3. **Bước 3 — Demo Giao diện & Đăng nhập phân quyền:**
   - Mở `http://127.0.0.1:5500`.
   - Bấm nút điền nhanh `admin / 123456` và đăng nhập -> Hệ thống mở Dashboard thống kê và hiện thẻ Quản trị tài khoản (`GET /users`).
4. **Bước 4 — Demo Nghiệp vụ cốt lõi & Bắt lỗi hợp lệ (Validation):**
   - Nhập một vùng trồng mới:
     - Tên: *Vùng trồng Xoài Cát Chu Cao Lãnh*
     - Địa điểm: *Mỹ Xương, Cao Lãnh, Đồng Tháp*
     - Diện tích: `-5` (cố tình nhập số âm) -> Bấm Thêm -> Giao diện và API lập tức chặn và báo lỗi diện tích phải lớn hơn 0 (Đạt tiêu chí AC).
     - Sửa diện tích thành `3.5` -> Bấm Thêm -> Bảng dữ liệu cập nhật tức thì.
5. **Bước 5 — Chứng minh phân quyền an toàn dữ liệu:**
   - Đăng xuất -> Bấm điền nhanh đăng nhập bằng `farmer / 123456`.
   - Chỉ cho Mentor thấy: Thẻ *Quản trị tài khoản* bị ẩn, trên bảng vùng trồng **không có nút Xoá** (tránh rủi ro nông dân xoá nhầm dữ liệu của hệ thống).
