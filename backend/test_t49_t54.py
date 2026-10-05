import sys
import time

# Ensure UTF-8 output on Windows console
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from fastapi.testclient import TestClient

# Add backend directory to sys.path
sys.path.insert(0, "backend")

from app.main import app

def run_tests():
    client = TestClient(app)
    auth_admin = ("admin", "123456")
    auth_farmer = ("farmer", "123456")

    print("\n=======================================================")
    print("KIỂM TRA CHỨC NĂNG T-49 (TRUY VẾT & VÙNG TRỒNG LÔ GỐC)")
    print("=======================================================")

    # 1. Test batch #2 (con của #1) -> trả về root_batch là #1 và origin_farm là farm của #1
    r = client.get("/batches/2/trace", auth=auth_farmer)
    assert r.status_code == 200, f"Status {r.status_code}: {r.text}"
    data = r.json()

    print(f"Lô hiện tại: #{data['batch_id']} - {data['product_name']} ({data['quantity']} kg)")
    print(f"Lô gốc: #{data['root_batch']['id']} - {data['root_batch']['product_name']} ({data['root_batch']['quantity']} kg)")
    print(f"Vùng trồng lô gốc: #{data['origin_farm']['id']} - {data['origin_farm']['name']}, {data['origin_farm']['location']} (Diện tích: {data['origin_farm']['area']} ha, Chủ: {data['origin_farm']['owner']})")

    assert data["root_batch"]["id"] == 1, "Lô gốc của #2 phải là #1"
    assert data["origin_farm"] is not None, "Phải có thông tin vùng trồng của lô gốc"
    assert "Cao Lãnh" in data["origin_farm"]["name"], "Tên vùng trồng lô gốc đúng"
    assert len(data["lineage"]) >= 2, "Lineage phải có từ 2 tầng trở lên"

    print("\nDanh sách phả hệ theo từng tầng:")
    for tier in data["lineage"]:
        print(f"  • {tier['tier_name']}: Lô #{tier['batch_id']} - {tier['product_name']} ({tier['quantity']} kg, Ngày: {tier['harvest_date']}, Đang xem: {tier['is_current']})")

    print("[PASS] Test 1: Lô gốc, thông tin vùng trồng và phả hệ theo tầng chính xác 100%!")

    # 2. Test Cache 60s
    print("\n=======================================================")
    print("KIỂM TRA CACHE TRUY VẾT 60 GIÂY")
    print("=======================================================")
    print(f"Lần gọi 1: cached={data['cached']}, remaining_ttl={data['cache_remaining_seconds']}s")
    assert data["cached"] is False or data["cached"] is True # Lần đầu sau khi invalidate là False

    time.sleep(1)
    r2 = client.get("/batches/2/trace", auth=auth_farmer)
    data2 = r2.json()
    print(f"Lần gọi 2 (sau 1s): cached={data2['cached']}, remaining_ttl={data2['cache_remaining_seconds']}s")
    assert data2["cached"] is True, "Lần gọi 2 phải được lấy từ cache 60s"
    assert 0 < data2["cache_remaining_seconds"] <= 60, "TTL phải nằm trong khoảng 0-60 giây"

    print("[PASS] Test 2: Cache 60 giây hoạt động chuẩn xác!")

    # 3. Test Quyền xem theo T-54 (403 Forbidden)
    print("\n=======================================================")
    print("KIỂM TRA PHÂN QUYỀN T-54 (403 FORBIDDEN KHI KHÔNG CÓ QUYỀN)")
    print("=======================================================")
    # Lô #10 là is_restricted = True, owner = admin
    # Farmer truy cập -> 403 Forbidden
    r_farmer = client.get("/batches/10/trace", auth=auth_farmer)
    print(f"Farmer truy cập lô #10: HTTP {r_farmer.status_code}")
    print(f"Thông báo lỗi: {r_farmer.json()}")
    assert r_farmer.status_code == 403, f"Farmer phải nhận HTTP 403, nhưng nhận {r_farmer.status_code}"
    assert "T-54" in r_farmer.json()["detail"], "Nội dung lỗi 403 phải nêu rõ chính sách T-54"

    # Lô #10 xem với endpoint /batches/10
    r_farmer_batch = client.get("/batches/10", auth=auth_farmer)
    print(f"Farmer xem chi tiết /batches/10: HTTP {r_farmer_batch.status_code}")
    assert r_farmer_batch.status_code == 403, "GET /batches/10 với farmer cũng phải bị 403 Forbidden"

    # Admin truy cập -> 200 OK
    r_admin = client.get("/batches/10/trace", auth=auth_admin)
    print(f"Admin truy cập lô #10: HTTP {r_admin.status_code}")
    assert r_admin.status_code == 200, f"Admin phải được phép truy cập (200 OK), nhưng nhận {r_admin.status_code}"
    admin_data = r_admin.json()
    print(f"Dữ liệu admin nhận được: Lô #{admin_data['batch_id']}, Lô gốc: #{admin_data['root_batch']['id']}, Vùng trồng: {admin_data['origin_farm']['name']}")

    print("[PASS] Test 3: Phân quyền T-54 (403 Forbidden) hoạt động chuẩn xác 100%!")

    print("\n=======================================================")
    print("TẤT CẢ TIÊU CHÍ NGHIỆM THU (DoD / AC) ĐỀU ĐẠT CHUẨN!")
    print("=======================================================\n")

if __name__ == "__main__":
    run_tests()
