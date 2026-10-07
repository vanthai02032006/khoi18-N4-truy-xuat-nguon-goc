# Nhãn quá hạn trên danh sách bàn giao của bên giao và bên nhận

| Thuộc tính | Giá trị |
| --- | --- |
| Mã ticket | _(chưa xác định)_ |
| Loại | Feature (UI + nghiệp vụ) |
| Trạng thái | To Do |
| Phụ thuộc | **T-56 (SCRUM-72)** |
| Liên quan | T-38 (danh sách chờ) |

## 🎯 Mục tiêu & Mô tả công việc

Hiển thị nhãn quá hạn ở danh sách chờ (T-38) và trang chi tiết lô của bên giao.
Sự kiện xác nhận muộn ghi thêm trường `muon` vào nội dung.

## ✅ Tiêu chí nghiệm thu (DoD / AC)

- [ ] Nhãn hiển thị đúng ở cả hai bên giao và nhận.
- [ ] Xác nhận muộn vẫn thực hiện đổi chủ thành công nhưng ghi nhận rõ cờ quá hạn.

## 🛠️ Ràng buộc kỹ thuật & Phụ thuộc

- **Phụ thuộc task:** T-56 (SCRUM-72).
- **Lưu ý kỹ thuật:** Nhãn có cả màu sắc và chữ, không chỉ dùng mỗi màu sắc để người mù màu đọc được.
