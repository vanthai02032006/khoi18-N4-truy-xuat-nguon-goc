## 📌 Thông tin User Story
- **Mã Story (ID):** S-
- **Người thực hiện:** @
- **Thuộc Epic:** 
- **Người Review cùng cặp:** @
- **Link Backlog / Sheet:** [Quy chuẩn DoD & DoR](https://docs.google.com/spreadsheets/d/1Sxra7R73-E8HqbTmmK1JfJHI0d9BcNzA/edit?gid=800459381#gid=800459381)

---

## 📝 Nội dung thay đổi
- [ ] Tóm tắt ngắn gọn các file hoặc tính năng vừa làm...

---

## ✅ Tiêu chuẩn hoàn thành (Definition of Done - DoD)
> *Tất cả các mục dưới đây là bắt buộc phải thỏa mãn trước khi merge:*

### 1. Chất lượng mã nguồn & Quy trình
- [ ] **Code Review**: Đã được xem xét và duyệt (Approve) bởi ít nhất một thành viên khác (bạn cùng cặp / Tech Lead).
- [ ] **Unit Test & Độ phủ**: Đã viết unit test cho các nhánh logic mới; độ phủ (coverage) trên phần thay đổi không bị giảm.
- [ ] **CI Xanh**: Pipeline CI chạy đạt toàn bộ: `build`, `lint`, `typecheck`, `test`.
- [ ] **Bảo mật & Phụ thuộc**: Không có secret (API key, mật khẩu, token) trong mã nguồn; quét phụ thuộc sạch (dependency vulnerability clean).
- [ ] **Kiểm thử Staging**: Acceptance Criteria (AC) pass trên môi trường staging, không chỉ chạy trên máy cá nhân localhost.
- [ ] **Bảo mật dữ liệu Nông hộ**: Không log bất kỳ dữ liệu định danh nông hộ nào (PII: CCCD/CMND, SĐT cá nhân, địa chỉ nhà nông hộ).
- [ ] **Tài liệu**: README.md hoặc `.env.example` được cập nhật đầy đủ nếu thay đổi hành vi công khai (public API) hoặc thêm/sửa biến môi trường.

### 2. Kiểm thử Nghiệp vụ đặc thù (Chọn mục tương ứng với Story)
- [ ] **Story chạm sự kiện của lô**: Kiểm tra toàn vẹn chuỗi (hash chain) vẫn báo hợp lệ sau khi chạy.
- [ ] **Story chạm đồ thị phả hệ**: Có ca kiểm thử với bộ dữ liệu mẫu có đáp án đếm tay và ca kiểm tra chu trình (cycle detection).
- [ ] **Story chạm khối lượng**: Có test kiểm thử hai giao dịch đồng thời (concurrency / race condition test).

---

## 📸 Bằng chứng nghiệm thu (Staging / Unit Test / Logs)
<!-- Dán ảnh chụp màn hình kiểm thử AC trên staging, kết quả test, hoặc log sạch vào đây -->
