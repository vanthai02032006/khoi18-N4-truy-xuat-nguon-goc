## 📌 Thông tin User Story
- **Mã Story (ID):** S-42 (S-42.1, S-42.2, S-42.3, S-42.4, S-42.5)
- **Người thực hiện:** @TechLead
- **Thuộc Epic:** Phân hệ Cold Chain (Chuỗi cung ứng lạnh nông sản)
- **Người Review cùng cặp:** @Reviewer
- **Link Backlog / Sheet:** [Quy chuẩn DoD & DoR](https://docs.google.com/spreadsheets/d/1Sxra7R73-E8HqbTmmK1JfJHI0d9BcNzA/edit?gid=800459381#gid=800459381)

---

## 📝 Nội dung thay đổi
- Hoàn thiện toàn bộ phân hệ Cold Chain chuẩn S-42 (17 SP / 5 User Stories):
  - S-42.1: Thu nhận & deduplicate telemetry nhiệt độ/độ ẩm IoT theo trip.
  - S-42.2: Động cơ phát hiện vi phạm ngưỡng liên tục 30 phút, chống nhiễu 5 phút (debounce gap).
  - S-42.3: Quản lý vòng đời chuyến xe lạnh (Trip lifecycle, consignments & auto hash chain ON_BOARD/OFF_BOARD).
  - S-42.4: Cảnh báo chất lượng thời gian thực khi bàn giao (Handover warning RED/GREEN).
  - S-42.5: Báo cáo hiệu suất nhà vận chuyển (Carrier performance report & rating).
- Bổ sung bộ kiểm thử tự động 100% (88 unit test pass).

---

## ✅ Tiêu chuẩn hoàn thành (Definition of Done - DoD)
> *Tất cả các mục dưới đây là bắt buộc phải thỏa mãn trước khi merge:*

### 1. Chất lượng mã nguồn & Quy trình
- [x] **Code Review**: Sẵn sàng duyệt (Approve) bởi thành viên có quyền write.
- [x] **Unit Test & Độ phủ**: Đầy đủ 88 unit test, độ phủ tăng cường trên phân hệ Cold Chain.
- [x] **CI Xanh**: Pipeline CI chạy đạt toàn bộ: `build`, `lint`, `typecheck`, `test`.
- [x] **Bảo mật & Phụ thuộc**: Không có secret (API key, mật khẩu, token) trong mã nguồn; quét phụ thuộc sạch.
- [x] **Kiểm thử Staging**: Acceptance Criteria (AC) pass 100%.
- [x] **Bảo mật dữ liệu Nông hộ**: Không log bất kỳ dữ liệu định danh nông hộ nào.
- [x] **Tài liệu**: Cập nhật Swagger/OpenAPI docs đầy đủ 36 API endpoints.
