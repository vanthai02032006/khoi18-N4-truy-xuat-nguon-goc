# 📜 QUY ƯỚC LÀM VIỆC & PHÂN QUYỀN GIT (KHOI18-N4)
> **Dự án:** Truy xuất nguồn gốc và giám sát chuỗi lạnh nông sản  
> **Áp dụng cho:** Toàn bộ thành viên nhóm 4

---

## 1. Cơ cấu vai trò & Phân quyền
- **Product Owner (PO):** Tuấn Đình (Quản lý yêu cầu, Product Backlog, nghiệm thu Story theo tiêu chí AC).
- **Scrum Master (SM):** Nguyễn Thanh Sơn (Quản lý tiến độ, điều phối Sprint, duyệt và bấm nút Merge PR).
- **Tech Lead:** Văn Thái (Kiến trúc hệ thống, CI/CD, chuẩn mã nguồn).
- **Dev Team:** 8 thành viên phụ trách theo 4 cặp chuyên trách:
  - **Cặp 1 (Hạ tầng, Auth, Phân quyền):** Văn Thái & Dương Minh Quang (`S-01`, `S-02`, `S-04`, `S-05`, `S-31`)
  - **Cặp 2 (Vùng trồng, Thửa đất, Lô thu hoạch):** Bích Ngọc & Nguyễn Nhuận (`S-06`, `S-07`, `S-08`, `S-09`, `S-14`)
  - **Cặp 3 (Chuỗi băm Hash Chain, Bàn giao):** Nguyễn Phúc & Quan Hoang (`K-01`, `S-10`, `S-11`, `S-12`, `S-15`, `S-16`)
  - **Cặp 4 (Phả hệ Tách/Gộp, Thu hồi, Chuỗi lạnh):** Quang Phan & Thuyên Vũ (`S-17`, `S-18`, `S-19`, `S-21`, `S-26`, `S-33`)

---

## 2. Quy tắc phân nhánh (Branching Policy)
- **Tuyệt đối KHÔNG** commit hay push trực tiếp vào `main` và `develop`.
- Mọi nhánh tính năng phải tạo từ `develop`:
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

## 4. Quy trình nghiệm thu & Merge (Definition of Done)
1. Dev lấy code mới nhất từ `develop`, tạo nhánh `feature/...` để code và test trên máy cá nhân.
2. Đẩy code lên nhánh riêng và tạo Pull Request vào `develop`.
3. Pipeline CI chạy kiểm tra tự động phải báo **Xanh (Passed)**.
4. Bạn cùng cặp vào đọc code, kiểm tra tính năng và bấm **Approve**.
5. **Scrum Master (Nguyễn Thanh Sơn)** kiểm tra lần cuối và bấm nút **Merge**.
6. Người tạo PR bấm **Delete branch** trên GitHub sau khi đã merge thành công.
