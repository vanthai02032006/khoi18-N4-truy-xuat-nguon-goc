"""Kịch bản kiểm thử tự động cho Task T-31 / SCRUM-47:
- Kiểm tra tính toàn vẹn chuỗi sự kiện bằng hàm T-28 (verify_batch_chain)
- Kiểm tra danh sách sự kiện đầy đủ thông tin: Loại, Thời điểm, Tổ chức, Hash
- Kiểm tra kịch bản giả lập sửa lén SQL -> phát hiện vi phạm và trả về báo cáo chi tiết
- Kiểm tra kịch bản khôi phục chuỗi chuẩn 10 sự kiện
"""

import httpx
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:8000"

def run_tests():
    print("=" * 60)
    print("BẮT ĐẦU KIỂM THỬ TỰ ĐỘNG T-31 (SCRUM-47)")
    print("=" * 60)

    # 1. Kiểm tra API lấy danh sách sự kiện của Lô #1
    r = httpx.get(f"{BASE_URL}/batches/1/events")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    events = r.json()
    assert len(events) == 10, f"Expected 10 events, got {len(events)}"
    print(f"[PASS 1] Lấy thành công {len(events)} sự kiện của Lô #1.")

    # Kiểm tra mỗi mốc sự kiện có đầy đủ: Loại, Thời điểm, Tổ chức, Data, Hash
    for ev in events:
        assert "event_type" in ev and ev["event_type"], "Thiếu event_type"
        assert "timestamp" in ev and ev["timestamp"], "Thiếu timestamp"
        assert "organization" in ev and ev["organization"], "Thiếu organization"
        assert "data" in ev and ev["data"], "Thiếu data"
        assert "prev_hash" in ev and len(ev["prev_hash"]) == 64, "Thiếu prev_hash"
        assert "hash" in ev and len(ev["hash"]) == 64, "Thiếu hash"

    sample_ev = events[0]
    print(f"  • Mốc #1: Loại={sample_ev['event_type']} | Thời điểm={sample_ev['timestamp']} | Tổ chức={sample_ev['organization']}")

    # 2. Kiểm tra gọi hàm T-28 qua API /verify-chain khi chuỗi nguyên vẹn
    r = httpx.get(f"{BASE_URL}/batches/1/verify-chain")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    report = r.json()
    assert report["is_valid"] is True, "Chuỗi chuẩn phải hợp lệ (is_valid=True)"
    assert report["status"] == "VERIFIED", f"Expected VERIFIED, got {report['status']}"
    assert report["verified_count"] == 10, f"Expected verified_count=10, got {report['verified_count']}"
    print(f"[PASS 2] Hàm kiểm định T-28 xác nhận chuỗi nguyên vẹn: status={report['status']}, verified={report['verified_count']}/10.")

    # 3. Giả lập hành vi sửa lén SQL qua API /simulate-tamper
    r = httpx.post(
        f"{BASE_URL}/batches/1/simulate-tamper",
        auth=("farmer", "123456")
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    tamper_report = r.json()
    assert tamper_report["is_valid"] is False, "Sau khi sửa lén, is_valid phải là False"
    assert tamper_report["status"] == "TAMPERED", f"Expected TAMPERED, got {tamper_report['status']}"
    assert tamper_report["tamper_type"] == "DATA_MODIFIED", f"Expected DATA_MODIFIED, got {tamper_report['tamper_type']}"
    print(f"[PASS 3] Giả lập sửa lén SQL thành công:")
    print(f"  • is_valid: {tamper_report['is_valid']}")
    print(f"  • status: {tamper_report['status']}")
    print(f"  • tamper_type: {tamper_report['tamper_type']}")
    print(f"  • Vị trí vi phạm: Sự kiện #{tamper_report['tampered_event_id']}")
    print(f"  • Chi tiết: {tamper_report['detail']}")

    # Kiểm tra lại bằng GET /verify-chain
    r_check = httpx.get(f"{BASE_URL}/batches/1/verify-chain")
    assert r_check.json()["is_valid"] is False, "GET verify-chain phải trả về is_valid=False khi chuỗi bị sửa lén"
    print("[PASS 4] GET /verify-chain bắt chính xác 100% lỗi toàn vẹn sau khi sửa lén.")

    # 4. Khôi phục lại chuỗi chuẩn 10 sự kiện
    r_reset = httpx.post(
        f"{BASE_URL}/batches/1/reset-events",
        auth=("farmer", "123456")
    )
    assert r_reset.status_code == 200, f"Expected 200, got {r_reset.status_code}"
    reset_report = r_reset.json()
    assert reset_report["is_valid"] is True, "Sau khi reset, is_valid phải là True"
    assert reset_report["status"] == "VERIFIED"
    print(f"[PASS 5] Khôi phục chuỗi thành công: status={reset_report['status']}, is_valid={reset_report['is_valid']}.")

    print("\n" + "=" * 60)
    print("🎉 TẤT CẢ 5 BƯỚC KIỂM THỬ TỰ ĐỘNG ĐỀU ĐẠT 100% YÊU CẦU NGHIỆM THU!")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
