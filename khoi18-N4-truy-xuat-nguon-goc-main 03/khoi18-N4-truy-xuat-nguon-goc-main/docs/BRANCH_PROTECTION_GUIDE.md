# Hướng Dẫn Cấu Hình Branch Protection Rules & CI Quality Gate Trên GitHub

Tài liệu này hướng dẫn chi tiết cách kích hoạt cơ chế bảo vệ nhánh `main` để hiện thực hóa 3 kịch bản:
1. **Chặn merge khi test đỏ (CI fail)**
2. **Chặn merge khi chưa có ít nhất 1 người review (Require approvals)**
3. **Chặn push trực tiếp vào nhánh `main` (Reject direct push)**

---

## Cách 1: Thiết lập qua giao diện Web GitHub (Khuyên dùng)

Truy cập repository dự án trên GitHub: `https://github.com/vanthai02032006/khoi18-N4-truy-xuat-nguon-goc`

### Bước 1: Mở mục Quản lý nhánh
1. Vào tab **Settings** (Cài đặt) của repository.
2. Tại cột menu bên trái, chọn **Branches** (trong mục *Code and automation*).
3. Tại phần **Branch protection rules**, nhấn nút **Add branch protection rule** (hoặc **Add rule**).

### Bước 2: Điền cấu hình bảo vệ cho nhánh `main`

1. **Branch name pattern:** Điền `main` (hoặc `develop` nếu muốn bảo vệ cả develop).

2. **Bắt buộc dùng Pull Request & Review (Kịch bản 2 & 3):**
   - Tích chọn: **`Require a pull request before merging`**
   - Tích chọn: **`Require approvals`** -> Chọn số lượng: **`1`** (bắt buộc ít nhất 1 thành viên khác duyệt).
   - Tích chọn: **`Dismiss stale pull request approvals when new commits are pushed`** (tự động hủy duyệt nếu tác giả đẩy code mới).
   - Tích chọn: **`Require review from Code Owners`** (tùy chọn).

3. **Bắt buộc CI Test xanh mới cho merge (Kịch bản 1):**
   - Tích chọn: **`Require status checks to pass before merging`**
   - Tích chọn: **`Require branches to be up to date before merging`** (yêu cầu PR phải cập nhật code mới nhất từ main trước khi merge).
   - Tại ô tìm kiếm **Status checks that are required**, tìm và tích chọn job:
     - `Run Pytest & Quality Checks` (tên job trong file `.github/workflows/ci.yml`).
     *(Lưu ý: Nếu chưa thấy job hiện ra, hãy tạo 1 PR thử nghiệm đầu tiên để GitHub Actions chạy 1 lần, sau đó job sẽ xuất hiện trong danh sách tìm kiếm).*

4. **Chặn đẩy trực tiếp & Áp dụng với cả Quản trị viên:**
   - Tích chọn: **`Do not allow bypassing the above settings`** (Áp dụng các quy tắc này cho toàn bộ mọi người, bao gồm cả Admin/Owner).
   - Mặc định khi đã bật *Require a pull request before merging*, thao tác `git push origin main` sẽ bị GitHub từ chối tự động.

5. Nhấn **Create** (hoặc **Save changes**) ở cuối trang. Nhập mật khẩu tài khoản nếu GitHub yêu cầu xác nhận.

---

## Cách 2: Thiết lập tự động bằng GitHub CLI (`gh`)

Nếu máy có cài đặt GitHub CLI và đã đăng nhập (`gh auth login`), bạn có thể cấu hình nhanh bằng lệnh sau:

```bash
# Áp dụng branch protection rule cho nhánh main
gh api -X PUT /repos/vanthai02032006/khoi18-N4-truy-xuat-nguon-goc/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -f required_status_checks[strict]=true \
  -F "required_status_checks[contexts][]=Run Pytest & Quality Checks" \
  -f enforce_admins=true \
  -f required_pull_request_reviews[dismiss_stale_reviews]=true \
  -f required_pull_request_reviews[require_code_owner_reviews]=false \
  -f required_pull_request_reviews[required_approving_review_count]=1 \
  -f restrictions=null
```

---

## 3. Kiểm chứng thực tế (Verification Scenarios)

| Kịch bản | Thao tác thử nghiệm | Kết quả mong đợi |
| :--- | :--- | :--- |
| **1. Test đỏ** | Tạo branch mới, cố tình sửa 1 test trong `tests/` thành `assert 1 == 2`, commit và mở PR vào `main`. | GitHub Actions chạy và báo đỏ (Failure). Nút **Merge pull request** bị mờ/khóa xám, có biểu tượng ❌ đỏ. |
| **2. Chưa ai duyệt** | PR test xanh nhưng chưa ai bấm Approve. | Nút Merge bị khóa, hiện thông báo: *"Review required: At least 1 approving review is required"*. |
| **3. Push thẳng** | Tại local đứng ở nhánh `main`, gõ lệnh `git push origin main`. | Terminal báo lỗi: `remote: error: GH006: Protected branch hook declined...` và từ chối push. |
