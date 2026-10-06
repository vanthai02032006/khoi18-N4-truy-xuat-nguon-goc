# 📜 QUY ƯỚC LÀM VIỆC & QUY TẮC ĐẨY CODE LÊN GITHUB (KHOI18-N4)
> **Dự án:** Truy xuất nguồn gốc và giám sát chuỗi lạnh nông sản  
> **Áp dụng cho:** Toàn bộ thành viên nhóm 4  
> **Nguồn quy chuẩn:** [Google Sheets DoD & DoR](https://docs.google.com/spreadsheets/d/1Sxra7R73-E8HqbTmmK1JfJHI0d9BcNzA/edit?gid=800459381#gid=800459381)

---

## 1. Cơ cấu vai trò & Phân quyền
- **Product Owner (PO):** Tuấn Đình (Quản lý yêu cầu, Product Backlog, nghiệm thu Story theo tiêu chí AC).
- **Scrum Master (SM):** Nguyễn Thanh Sơn (Quản lý tiến độ, điều phối Sprint, duyệt và bấm nút Merge PR).
- **Tech Lead:** Văn Thái (Kiến trúc hệ thống, CI/CD, chuẩn mã nguồn và bảo mật).
- **Dev Team:** 8 thành viên phụ trách theo 4 cặp chuyên trách:
  - **Cặp 1 (Hạ tầng, Auth, Phân quyền):** Văn Thái & Dương Minh Quang (`S-01`, `S-02`, `S-04`, `S-05`, `S-31`)
  - **Cặp 2 (Vùng trồng, Thửa đất, Lô thu hoạch):** Bích Ngọc & Nguyễn Nhuận (`S-06`, `S-07`, `S-08`, `S-09`, `S-14`)
  - **Cặp 3 (Chuỗi băm Hash Chain, Bàn giao):** Nguyễn Phúc & Quân Hoàng (`K-01`, `S-10`, `S-11`, `S-12`, `S-15`, `S-16`)
  - **Cặp 4 (Phả hệ Tách/Gộp, Thu hồi, Chuỗi lạnh):** Quang Phan & Thuyên Vũ (`S-17`, `S-18`, `S-19`, `S-21`, `S-26`, `S-33`)

---

## 2. Quy tắc phân nhánh (Branching Policy)
- **Tuyệt đối KHÔNG** commit hay push trực tiếp vào `main` và `develop`.
- Mọi nhánh tính năng phải bắt nguồn từ `develop`:
  - Tính năng mới: `feature/<Mã-Story>-<tên-ngắn-gọn>`  
    *Ví dụ: `feature/S-04-dang-nhap`, `feature/S-08-ghi-nhan-lo`*
  - Sửa lỗi: `bugfix/<Mã-Story>-<tên-lỗi>`  
    *Ví dụ: `bugfix/S-08-fix-ma-lo-trung`*

---

## 3. Quy chuẩn đặt tên Commit (Conventional Commits)
- `feat(S-xx): <nội dung>`: Thêm tính năng mới.
- `fix(S-xx): <nội dung>`: Sửa lỗi.
- `test(S-xx): <nội dung>`: Viết test hoặc seed dữ liệu mẫu.
- `docs(S-xx): <nội dung>`: Viết tài liệu / báo cáo Spike.

*Ví dụ chuẩn:*
```bash
git commit -m "feat(S-08): sinh ma lo thu hoach ngau nhien va validate du lieu"
```

---

## 4. Definition of Ready (DoR) - Tiêu chí sẵn sàng đưa vào Sprint
*Được kiểm soát qua Issue Template `.github/ISSUE_TEMPLATE/user_story.md`:*

| STT | Mục DoR | Được phép chặn | Hướng dẫn thực hiện |
| :---: | :--- | :---: | :--- |
| 1 | **Đủ nhỏ để Done trong 1 sprint** | **Có (Chặn)** | Story quá to phải tách thành các story nhỏ hơn để hoàn thành trong 1 sprint |
| 2 | **Dependency ngoài đã có cam kết** | **Có (Chặn)** | Nếu phụ thuộc vào bên ngoài (API, IoT, dữ liệu mẫu), phải có cam kết sẵn sàng |
| 3 | **Có AC viết dạng Giả sử / Khi / Thì** | Không | Viết AC theo định dạng: *Giả sử (Given) ... Khi (When) ... Thì (Then) ...* |
| 4 | **Đã ước lượng story point bởi chính team** | Không | Dùng Planning Poker bởi team, không lấy SP thô áp đặt từ backlog |
| 5 | **Team hiểu story nói gì** | Không | Không cần phải hỏi lại người viết sau buổi Grooming |
| 6 | **Team thực tập đã chẻ task con $\le$ nửa ngày** | Không | Mọi task con của intern $\le$ 4 giờ làm việc |

---

## 5. Definition of Done (DoD) - Tiêu chuẩn hoàn thành để Merge PR
*Được kiểm soát qua PR Template `.github/pull_request_template.md` & GitHub Actions CI:*

| STT | Mục DoD | Hình thức kiểm soát |
| :---: | :--- | :--- |
| 1 | **Code review đã duyệt bởi ít nhất một thành viên khác** | GitHub Ruleset: Bắt buộc tối thiểu 1 Approval từ bạn cùng cặp / Reviewer |
| 2 | **Unit test cho nhánh logic mới; độ phủ trên phần thay đổi không giảm** | CI pytest-cov kiểm tra test coverage |
| 3 | **CI xanh: build, lint, typecheck, test** | GitHub Actions Pipeline bắt buộc Passed |
| 4 | **Không có secret trong mã nguồn; quét phụ thuộc sạch** | Gitleaks action chặn secret + pip-audit quét phụ thuộc an toàn |
| 5 | **AC pass trên môi trường staging, không chỉ trên máy cá nhân** | Kiểm thử xác nhận trên Staging, đính kèm bằng chứng vào PR |
| 6 | **Story chạm sự kiện của lô: kiểm tra toàn vẹn chuỗi vẫn báo hợp lệ sau khi chạy** | Test integrity chuỗi băm (Hash chain validation) |
| 7 | **Story chạm đồ thị phả hệ: có ca kiểm thử với bộ dữ liệu mẫu có đáp án đếm tay và ca chu trình** | Test phả hệ: Directed Acyclic Graph (DAG), bắt chu trình (cycle detection) |
| 8 | **Story chạm khối lượng: có test hai giao dịch đồng thời** | Test concurrency / race conditions giao dịch |
| 9 | **Không log dữ liệu định danh nông hộ** | CI PII scanner + Code review (không in CCCD, SĐT, định danh cá nhân) |
| 10 | **README cập nhật nếu đổi hành vi công khai hoặc thêm biến môi trường** | Bắt buộc cập nhật tài liệu và `.env.example` |

---

## 6. Quy trình tạo Pull Request & Nghiệm thu
1. Dev lấy code mới nhất từ `develop`:
   ```bash
   git checkout develop
   git pull origin develop
   git checkout -b feature/S-xx-ten-tinh-nang
   ```
2. Viết mã nguồn, viết Unit test, chạy test nội bộ trước khi push:
   ```bash
   flake8 backend
   pytest backend/tests
   ```
3. Đẩy lên nhánh riêng và mở Pull Request vào nhánh `develop`.
4. Điền đầy đủ Checklist trong PR template.
5. Chờ CI chạy xanh (Passed).
6. Thành viên cùng cặp hoặc Tech Lead đọc code, kiểm tra tính năng và bấm **Approve**.
7. **Scrum Master (Nguyễn Thanh Sơn)** kiểm tra lần cuối và bấm **Merge**.
