"""Bộ kiểm thử cho chức năng 'Nhập sản phẩm và thửa từ tệp CSV'.

Kiểm tra theo các kịch bản BDD:
1. Kịch bản thành công: Tệp CSV đúng mẫu, mọi dòng được tạo trong CSDL, trả về tổng số dòng.
2. Kịch bản thất bại (Rollback Transaction):
   Nếu có MỘT dòng bất kỳ sai data, KHÔNG CÓ dòng nào được tạo (rollback toàn bộ)
   và API trả về thông báo lỗi nêu rõ dòng và cột bị sai.
3. Hỗ trợ tiêu đề tiếng Anh, tiếng Việt, phát hiện trùng lặp sản phẩm trong CSV và CSDL.
"""

import io
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Farm, Product, ROLE_ADMIN, ROLE_FARMER, User
from app.security import hash_password


@pytest.fixture
def csv_test_env():
    """Tạo môi trường kiểm thử SQLite in-memory biệt lập."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Khởi tạo user admin & farmer
    db = TestingSessionLocal()
    admin = User(username="admin", password=hash_password("123456"), role=ROLE_ADMIN)
    farmer = User(username="farmer", password=hash_password("123456"), role=ROLE_FARMER)
    db.add_all([admin, farmer])
    db.commit()
    db.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client, TestingSessionLocal
    app.dependency_overrides.clear()


def test_csv_import_success(csv_test_env):
    """Kịch bản thành công: Tệp CSV hợp lệ, tạo đầy đủ sản phẩm và thửa đất."""
    client, SessionLocal = csv_test_env

    csv_data = (
        "type,name,unit,description,location,area,owner\n"
        "product,Xoài Cát Chu Cao Lãnh,kg,Xoài cát chu chuẩn VietGAP,,,\n"
        "product,Sầu riêng Ri6 Cái Mơn,kg,Sầu riêng cơm vàng hạt lép,,,\n"
        "farm,Vườn Xoài Mỹ Xương 1,,,Xã Mỹ Xương Cao Lãnh,3.5,Hợp tác xã Xoài Mỹ Xương\n"
        "farm,Vùng Canh Tác Sầu Riêng 2,,,Xã Bình Hàng Tây Cao Lãnh,2.0,Nông dân Nguyễn Văn An\n"
    )

    files = {"file": ("danh_sach_hop_tac_xa.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}
    response = client.post("/imports/csv", files=files, auth=("farmer", "123456"))

    assert response.status_code == 200
    res_json = response.json()
    assert res_json["status"] == "success"
    assert res_json["total_imported"] == 4
    assert res_json["products_imported"] == 2
    assert res_json["farms_imported"] == 2

    # Xác thực trong cơ sở dữ liệu đã có đủ 4 bản ghi
    with SessionLocal() as db:
        prods = list(db.scalars(select(Product)).all())
        farms = list(db.scalars(select(Farm)).all())
        assert len(prods) == 2
        assert len(farms) == 2
        prod_names = [p.name for p in prods]
        assert "Xoài Cát Chu Cao Lãnh" in prod_names
        assert "Sầu riêng Ri6 Cái Mơn" in prod_names
        farm_names = [f.name for f in farms]
        assert "Vườn Xoài Mỹ Xương 1" in farm_names
        assert "Vùng Canh Tác Sầu Riêng 2" in farm_names


def test_csv_import_failure_rollback_single_row_error(csv_test_env):
    """Kịch bản thất bại: Có 1 dòng thửa bị sai diện tích (âm), toàn bộ transaction phải rollback."""
    client, SessionLocal = csv_test_env

    csv_data = (
        "type,name,unit,description,location,area,owner\n"
        "product,Thanh Long Ruột Đỏ,kg,Thanh long VietGAP,,,\n"
        "product,Bưởi Da Xanh,kg,Bưởi da xanh loại 1,,,\n"
        "farm,Vườn Bưởi Ba Tri,,,Bến Tre,-2.5,Hộ Nông Dân\n"  # Dòng 4 sai diện tích <= 0
        "farm,Vườn Thanh Long Chợ Gạo,,,Tiền Giang,1.5,Hợp tác xã Chợ Gạo\n"
    )

    files = {"file": ("data_sai.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}
    response = client.post("/imports/csv", files=files, auth=("admin", "123456"))

    assert response.status_code == 400
    res_json = response.json()
    assert "detail" in res_json
    detail = res_json["detail"]
    assert "errors" in detail
    errors = detail["errors"]

    # Xác định dòng và cột bị sai
    assert any(err["row"] == 4 and "Diện tích" in err["column"] for err in errors)

    # QUAN TRỌNG: Xác thực Database đã ROLLBACK (không có sản phẩm hay thửa nào được tạo)
    with SessionLocal() as db:
        prods = list(db.scalars(select(Product)).all())
        farms = list(db.scalars(select(Farm)).all())
        assert len(prods) == 0, "Lỗi transaction: Sản phẩm không được tạo khi có dòng bị sai"
        assert len(farms) == 0, "Lỗi transaction: Thửa đất không được tạo khi có dòng bị sai"


def test_csv_import_failure_product_missing_name_rollback(csv_test_env):
    """Kịch bản thất bại: Dòng sản phẩm bị thiếu tên sản phẩm -> rollback."""
    client, SessionLocal = csv_test_env

    csv_data = (
        "type,name,unit,description,location,area,owner\n"
        "product,,kg,Không có tên sản phẩm,,,\n"  # Dòng 2 thiếu tên
        "farm,Thửa Đất Mẫu 1,,,Cao Lãnh,2.5,Nông Dân A\n"
    )

    files = {"file": ("missing_name.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}
    response = client.post("/imports/csv", files=files, auth=("farmer", "123456"))

    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert any(err["row"] == 2 and "Tên sản phẩm" in err["column"] for err in errors)

    # Đảm bảo rollback sạch
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Product)).all())) == 0
        assert len(list(db.scalars(select(Farm)).all())) == 0


def test_csv_import_failure_product_invalid_unit_rollback(csv_test_env):
    """Kịch bản thất bại: Dòng sản phẩm có đơn vị tính không hợp lệ -> rollback."""
    client, SessionLocal = csv_test_env

    csv_data = (
        "type,name,unit,description,location,area,owner\n"
        "product,Chôm Chôm Nhãn,don_vi_la,,,\n"  # Dòng 2 sai đơn vị tính
        "farm,Vườn Chôm Chôm Long Khánh,,,Đồng Nai,1.0,HTX Long Khánh\n"
    )

    files = {"file": ("invalid_unit.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}
    response = client.post("/imports/csv", files=files, auth=("farmer", "123456"))

    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert any(err["row"] == 2 and "Đơn vị tính" in err["column"] for err in errors)

    with SessionLocal() as db:
        assert len(list(db.scalars(select(Product)).all())) == 0
        assert len(list(db.scalars(select(Farm)).all())) == 0


def test_csv_import_failure_duplicate_product_in_csv_rollback(csv_test_env):
    """Kịch bản thất bại: Trùng lặp tên sản phẩm ngay trong cùng 1 tệp CSV."""
    client, SessionLocal = csv_test_env

    csv_data = (
        "type,name,unit,description,location,area,owner\n"
        "product,Mãng Cầu Xiêm,kg,Loại 1,,,\n"
        "product,Mãng Cầu Xiêm,kg,Loại 2 trùng tên,,,\n"  # Dòng 3 trùng tên với dòng 2
    )

    files = {"file": ("dup_csv.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}
    response = client.post("/imports/csv", files=files, auth=("farmer", "123456"))

    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert any(err["row"] == 3 and "trùng lặp" in err["message"].lower() for err in errors)

    with SessionLocal() as db:
        assert len(list(db.scalars(select(Product)).all())) == 0


def test_csv_import_failure_duplicate_product_in_db_rollback(csv_test_env):
    """Kịch bản thất bại: Tên sản phẩm đã tồn tại trong CSDL từ trước."""
    client, SessionLocal = csv_test_env

    # Thêm sẵn 1 sản phẩm vào CSDL
    with SessionLocal() as db:
        db.add(Product(name="Cam Sành Hàm Yên", unit="kg"))
        db.commit()

    csv_data = (
        "type,name,unit,description,location,area,owner\n"
        "product,Cam Sành Hàm Yên,kg,Thêm lại trùng tên,,,\n"
        "farm,Vườn Cam Hàm Yên,,,Tuyên Quang,5.0,HTX Cam\n"
    )

    files = {"file": ("dup_db.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}
    response = client.post("/imports/csv", files=files, auth=("farmer", "123456"))

    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert any(err["row"] == 2 and "đã tồn tại" in err["message"].lower() for err in errors)

    # Thửa đất không được tạo do rollback
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Farm)).all())) == 0


def test_csv_import_vietnamese_headers_success(csv_test_env):
    """Hỗ trợ tệp CSV với tiêu đề cột tiếng Việt."""
    client, SessionLocal = csv_test_env

    csv_data = (
        "loại,tên,đơn vị tính,mô tả,địa điểm,diện tích,chủ sở hữu\n"
        "sản phẩm,Nhãn Xuồng Cơm Vàng,kg,Nhãn xuồng Bà Rịa Vũng Tàu,,,\n"
        "thửa đất,Vườn Nhãn Xuyên Mộc,,,Xuyên Mộc Bà Rịa,4.2,Hợp tác xã Nông nghiệp\n"
    )

    files = {"file": ("tieng_viet.csv", io.BytesIO(csv_data.encode("utf-8-sig")), "text/csv")}
    response = client.post("/imports/csv", files=files, auth=("farmer", "123456"))

    assert response.status_code == 200
    assert response.json()["total_imported"] == 2

    with SessionLocal() as db:
        prod = db.scalar(select(Product).where(Product.name == "Nhãn Xuồng Cơm Vàng"))
        farm = db.scalar(select(Farm).where(Farm.name == "Vườn Nhãn Xuyên Mộc"))
        assert prod is not None
        assert farm is not None
        assert farm.area == 4.2


def test_csv_import_empty_file_and_invalid_extension(csv_test_env):
    """Kiểm tra chặn tệp không phải CSV hoặc tệp rỗng."""
    client, _ = csv_test_env

    # 1. Sai đuôi file
    files = {"file": ("data.txt", io.BytesIO(b"abc,def"), "text/plain")}
    res1 = client.post("/imports/csv", files=files, auth=("farmer", "123456"))
    assert res1.status_code == 400

    # 2. File rỗng
    files_empty = {"file": ("empty.csv", io.BytesIO(b""), "text/csv")}
    res2 = client.post("/imports/csv", files=files_empty, auth=("farmer", "123456"))
    assert res2.status_code == 400


def test_csv_import_delegated_endpoints(csv_test_env):
    """Kiểm tra các endpoint phụ /products/import-csv và /farms/import-csv."""
    client, _ = csv_test_env

    csv_data = (
        "type,name,unit,description,location,area,owner\n"
        "product,Hồng Không Hạt Đà Lạt,kg,Hồng giòn Đà Lạt,,,\n"
        "farm,Vườn Hồng Cầu Đất,,,Đà Lạt Lâm Đồng,1.8,HTX Cầu Đất\n"
    )

    # Gọi qua /products/import-csv
    files1 = {"file": ("products_import.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}
    res1 = client.post("/products/import-csv", files=files1, auth=("farmer", "123456"))
    assert res1.status_code == 200
    assert res1.json()["total_imported"] == 2

    # Tải tệp CSV mẫu chuẩn
    res_template = client.get("/imports/template-csv")
    assert res_template.status_code == 200
    assert "mau_nhap_san_pham_va_thua.csv" in res_template.headers.get("content-disposition", "")
    assert "product" in res_template.text
    assert "farm" in res_template.text
