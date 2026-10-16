"""Router xử lý nhập dữ liệu hàng loạt từ tệp CSV (Sản phẩm & Thửa đất).

Đáp ứng yêu cầu nghiệp vụ:
1. Upload file CSV để tạo hàng loạt Sản phẩm (Products) và Thửa đất (Farms).
2. Kịch bản thành công: Mọi dòng đều hợp lệ, lưu toàn bộ vào CSDL, trả về tổng số dòng.
3. Kịch bản thất bại: Nếu có MỘT dòng bất kỳ sai data, KHÔNG CÓ dòng nào được tạo
   (gọi session.rollback()) và throw HTTPException chứa chi tiết mảng lỗi (tên cột, dòng số).
"""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Farm, Product, ProductUnit, PRODUCT_UNITS, User
from app.schemas import CsvImportResponse, CsvRowError, FarmCreate, ProductCreate
from app.security import require_farmer

router = APIRouter(
    prefix="/imports",
    tags=["Imports"],
)

# Ánh xạ tên đơn vị tính tiếng Việt phổ biến sang mã ProductUnit chuẩn
_VI_UNIT_MAP: dict[str, str] = {
    "kg": ProductUnit.KG.value,
    "kilogram": ProductUnit.KG.value,
    "kí": ProductUnit.KG.value,
    "ký": ProductUnit.KG.value,
    "g": ProductUnit.G.value,
    "gram": ProductUnit.G.value,
    "gam": ProductUnit.G.value,
    "ton": ProductUnit.TON.value,
    "tấn": ProductUnit.TON.value,
    "tan": ProductUnit.TON.value,
    "liter": ProductUnit.LITER.value,
    "lít": ProductUnit.LITER.value,
    "lit": ProductUnit.LITER.value,
    "l": ProductUnit.LITER.value,
    "box": ProductUnit.BOX.value,
    "thùng": ProductUnit.BOX.value,
    "thung": ProductUnit.BOX.value,
    "hộp": ProductUnit.BOX.value,
    "hop": ProductUnit.BOX.value,
    "bottle": ProductUnit.BOTTLE.value,
    "chai": ProductUnit.BOTTLE.value,
    "lọ": ProductUnit.BOTTLE.value,
    "lo": ProductUnit.BOTTLE.value,
    "piece": ProductUnit.PIECE.value,
    "cái": ProductUnit.PIECE.value,
    "cai": ProductUnit.PIECE.value,
    "quả": ProductUnit.PIECE.value,
    "qua": ProductUnit.PIECE.value,
    "trái": ProductUnit.PIECE.value,
    "trai": ProductUnit.PIECE.value,
    "bundle": ProductUnit.BUNDLE.value,
    "bó": ProductUnit.BUNDLE.value,
    "bo": ProductUnit.BUNDLE.value,
}

# Ánh xạ tiêu đề cột trong file CSV (hỗ trợ cả tiếng Anh lẫn tiếng Việt)
_HEADER_MAP: dict[str, str] = {
    "type": "type",
    "loai": "type",
    "loại": "type",
    "loai_doi_tuong": "type",
    "loại đối tượng": "type",
    "entity_type": "type",
    "name": "name",
    "ten": "name",
    "tên": "name",
    "ten_san_pham": "name",
    "tên sản phẩm": "name",
    "ten_thua": "name",
    "tên thửa": "name",
    "ten_vung_trong": "name",
    "tên vùng trồng": "name",
    "unit": "unit",
    "don_vi_tinh": "unit",
    "đơn vị tính": "unit",
    "don vi tinh": "unit",
    "dvt": "unit",
    "description": "description",
    "mo_ta": "description",
    "mô tả": "description",
    "ghi_chu": "description",
    "ghi chú": "description",
    "location": "location",
    "dia_diem": "location",
    "địa điểm": "location",
    "vi_tri": "location",
    "vị trí": "location",
    "area": "area",
    "dien_tich": "area",
    "diện tích": "area",
    "dien tich": "area",
    "owner": "owner",
    "chu_so_huu": "owner",
    "chủ sở hữu": "owner",
    "chu_thua": "owner",
    "chủ thửa": "owner",
    "nong_dan": "owner",
    "nông dân": "owner",
}


def _normalize_header(raw_header: str) -> str:
    """Chuẩn hóa tên cột tiêu đề về định dạng canonical."""
    cleaned = raw_header.strip().lower()
    return _HEADER_MAP.get(cleaned, cleaned)


def _normalize_unit(raw_unit: str) -> str:
    """Chuẩn hóa đơn vị tính về mã chuẩn trong ProductUnit."""
    cleaned = raw_unit.strip().lower()
    return _VI_UNIT_MAP.get(cleaned, cleaned)


@router.post(
    "/csv",
    response_model=CsvImportResponse,
    status_code=status.HTTP_200_OK,
    summary="Nhập sản phẩm và thửa đất từ tệp CSV",
    description=(
        "Tải lên tệp CSV để tạo hàng loạt Sản phẩm (Products) và Thửa đất (Farms) "
        "cho hợp tác xã mới.\n\n"
        "**Nguyên tắc Transaction:** Nếu có bất kỳ dòng nào sai dữ liệu, toàn bộ giao dịch "
        "bị rollback (không tạo dòng nào) và trả về danh sách chi tiết các dòng/cột bị lỗi."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Dữ liệu tệp CSV không hợp lệ (kèm chi tiết lỗi)."},
        status.HTTP_401_UNAUTHORIZED: {"description": "Chưa đăng nhập."},
        status.HTTP_403_FORBIDDEN: {"description": "Không có quyền thực hiện."},
    },
)
async def import_products_and_farms_csv(
    file: UploadFile = File(..., description="Tệp tin CSV (.csv) chứa danh sách sản phẩm và thửa đất."),
    current_user: User = Depends(require_farmer),
    db: Session = Depends(get_db),
) -> CsvImportResponse:
    """Endpoint upload CSV để nhập hàng loạt sản phẩm và thửa đất."""
    _ = current_user

    # 1. Kiểm tra định dạng tệp tin
    if not file.filename or not file.filename.lower().endswith(".csv"):
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Tệp tải lên không đúng định dạng CSV (.csv).",
                "errors": [{"row": 0, "column": "Tệp tin", "message": "Vui lòng chọn tệp định dạng .csv"}],
            },
        )

    # 2. Đọc nội dung file và giải mã chuỗi ký tự UTF-8
    try:
        content_bytes = await file.read()
    except Exception as read_err:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": f"Không thể đọc tệp CSV: {read_err}",
                "errors": [{"row": 0, "column": "Tệp tin", "message": str(read_err)}],
            },
        )

    text_content = ""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text_content = content_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if not text_content:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Không thể giải mã tệp tin CSV (yêu cầu mã hóa UTF-8).",
                "errors": [{"row": 0, "column": "Tệp tin", "message": "Không đọc được bảng mã UTF-8."}],
            },
        )

    # 3. Phân tích nội dung CSV bằng csv.reader
    stream = io.StringIO(text_content)
    try:
        csv_reader = csv.reader(stream)
        raw_headers = next(csv_reader, None)
    except Exception as parse_err:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": f"Tệp CSV bị lỗi định dạng: {parse_err}",
                "errors": [{"row": 1, "column": "Tiêu đề", "message": str(parse_err)}],
            },
        )

    if not raw_headers:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Tệp CSV rỗng (không có tiêu đề và dữ liệu).",
                "errors": [{"row": 0, "column": "Tệp tin", "message": "Tệp CSV không có dữ liệu."}],
            },
        )

    headers = [_normalize_header(h) for h in raw_headers]

    errors: list[CsvRowError] = []
    pending_products: list[Product] = []
    pending_farms: list[Farm] = []

    # Theo dõi trùng tên sản phẩm trong file CSV và trong database
    seen_csv_product_names: dict[str, int] = {}
    existing_db_products: set[str] = {
        name.lower() for name in db.scalars(select(Product.name)).all()
    }

    line_number = 1  # Dòng 1 là header

    for row_values in csv_reader:
        line_number += 1

        # Bỏ qua dòng trống hoàn toàn
        if not row_values or not any(v.strip() for v in row_values):
            continue

        # Ánh xạ dòng theo dictionary cột
        row_dict: dict[str, str] = {}
        for idx, col_name in enumerate(headers):
            val = row_values[idx].strip() if idx < len(row_values) else ""
            row_dict[col_name] = val

        # Xác định loại đối tượng (Product hay Farm)
        row_type = row_dict.get("type", "").strip().lower()
        if not row_type:
            # Tự động suy luận nếu không có cột type
            if "unit" in headers:
                row_type = "product"
            elif any(k in headers for k in ("location", "area", "owner")):
                row_type = "farm"
            else:
                errors.append(
                    CsvRowError(
                        row=line_number,
                        column="Loại đối tượng",
                        message="Không xác định được loại đối tượng (thiếu cột 'type' hoặc 'loại').",
                    )
                )
                continue

        # --- XỬ LÝ SẢN PHẨM (PRODUCT) ---
        if row_type in ("product", "san_pham", "san pham", "sản phẩm", "sp"):
            raw_name = row_dict.get("name", "").strip()
            raw_unit = row_dict.get("unit", "").strip()
            raw_desc = row_dict.get("description", "").strip() or None

            # 1. Kiểm tra bắt buộc tên sản phẩm
            if not raw_name:
                errors.append(
                    CsvRowError(
                        row=line_number,
                        column="Tên sản phẩm",
                        message="Bắt buộc nhập tên sản phẩm.",
                    )
                )
            else:
                # Kiểm tra trùng lặp trong tệp CSV
                normalized_name = raw_name.lower()
                if normalized_name in seen_csv_product_names:
                    errors.append(
                        CsvRowError(
                            row=line_number,
                            column="Tên sản phẩm",
                            message=(
                                f"Tên sản phẩm '{raw_name}' bị trùng lặp trong tệp CSV "
                                f"(đã xuất hiện ở dòng {seen_csv_product_names[normalized_name]})."
                            ),
                        )
                    )
                else:
                    seen_csv_product_names[normalized_name] = line_number

                # Kiểm tra đã tồn tại trong CSDL
                if normalized_name in existing_db_products:
                    errors.append(
                        CsvRowError(
                            row=line_number,
                            column="Tên sản phẩm",
                            message=f"Sản phẩm '{raw_name}' đã tồn tại trong danh mục hệ thống.",
                        )
                    )

            # 2. Kiểm tra đơn vị tính
            norm_unit = _normalize_unit(raw_unit) if raw_unit else ""
            if not raw_unit:
                errors.append(
                    CsvRowError(
                        row=line_number,
                        column="Đơn vị tính",
                        message="Bắt buộc nhập đơn vị tính.",
                    )
                )
            elif norm_unit not in PRODUCT_UNITS:
                errors.append(
                    CsvRowError(
                        row=line_number,
                        column="Đơn vị tính",
                        message=(
                            f"Đơn vị tính '{raw_unit}' không hợp lệ. "
                            f"Cho phép: {', '.join(PRODUCT_UNITS)}."
                        ),
                    )
                )

            # 3. Validate qua Pydantic ProductCreate nếu các trường cơ bản đã có
            if raw_name and norm_unit in PRODUCT_UNITS:
                try:
                    product_dto = ProductCreate(
                        name=raw_name,
                        unit=norm_unit,  # type: ignore[arg-type]
                        description=raw_desc,
                    )
                    pending_products.append(
                        Product(
                            name=product_dto.name,
                            unit=product_dto.unit,
                            description=product_dto.description,
                        )
                    )
                except ValidationError as val_err:
                    for err in val_err.errors():
                        loc = err.get("loc", [""])[0]
                        field_label = "Tên sản phẩm" if loc == "name" else ("Đơn vị tính" if loc == "unit" else "Mô tả")
                        errors.append(
                            CsvRowError(
                                row=line_number,
                                column=field_label,
                                message=str(err.get("msg", "Dữ liệu không hợp lệ.")),
                            )
                        )

        # --- XỬ LÝ THỬA ĐẤT (FARM / PLOT) ---
        elif row_type in ("farm", "thua", "thửa", "thửa đất", "thua dat", "vung_trong", "vùng trồng", "plot"):
            raw_name = row_dict.get("name", "").strip()
            raw_location = row_dict.get("location", "").strip()
            raw_area = row_dict.get("area", "").strip().replace(",", ".")
            raw_owner = row_dict.get("owner", "").strip()

            # 1. Tên thửa đất
            if not raw_name:
                errors.append(
                    CsvRowError(
                        row=line_number,
                        column="Tên thửa đất",
                        message="Bắt buộc nhập tên thửa đất / vùng trồng.",
                    )
                )

            # 2. Vị trí thửa đất
            if not raw_location:
                errors.append(
                    CsvRowError(
                        row=line_number,
                        column="Vị trí thửa đất",
                        message="Bắt buộc nhập vị trí / địa điểm thửa đất.",
                    )
                )

            # 3. Chủ sở hữu
            if not raw_owner:
                errors.append(
                    CsvRowError(
                        row=line_number,
                        column="Chủ thửa đất",
                        message="Bắt buộc nhập chủ thửa đất / hộ nông dân.",
                    )
                )

            # 4. Diện tích
            area_val: float | None = None
            if not raw_area:
                errors.append(
                    CsvRowError(
                        row=line_number,
                        column="Diện tích",
                        message="Bắt buộc nhập diện tích canh tác.",
                    )
                )
            else:
                try:
                    area_val = float(raw_area)
                    if area_val <= 0:
                        errors.append(
                            CsvRowError(
                                row=line_number,
                                column="Diện tích",
                                message="Diện tích canh tác phải lớn hơn 0 (ha).",
                            )
                        )
                except ValueError:
                    errors.append(
                        CsvRowError(
                            row=line_number,
                            column="Diện tích",
                            message="Diện tích canh tác phải là định dạng số hợp lệ (ha).",
                        )
                    )

            # 5. Validate qua Pydantic FarmCreate
            if raw_name and raw_location and raw_owner and (area_val is not None and area_val > 0):
                try:
                    farm_dto = FarmCreate(
                        name=raw_name,
                        location=raw_location,
                        area=area_val,
                        owner=raw_owner,
                    )
                    pending_farms.append(
                        Farm(
                            name=farm_dto.name,
                            location=farm_dto.location,
                            area=farm_dto.area,
                            owner=farm_dto.owner,
                        )
                    )
                except ValidationError as val_err:
                    for err in val_err.errors():
                        loc = err.get("loc", [""])[0]
                        field_map = {
                            "name": "Tên thửa đất",
                            "location": "Vị trí thửa đất",
                            "area": "Diện tích",
                            "owner": "Chủ thửa đất",
                        }
                        errors.append(
                            CsvRowError(
                                row=line_number,
                                column=field_map.get(str(loc), str(loc)),
                                message=str(err.get("msg", "Dữ liệu không hợp lệ.")),
                            )
                        )

        # --- LOẠI ĐỐI TƯỢNG KHÔNG HỢP LỆ ---
        else:
            errors.append(
                CsvRowError(
                    row=line_number,
                    column="Loại đối tượng",
                    message=(
                        f"Loại đối tượng '{row_type}' không hợp lệ. "
                        "Vui lòng ghi 'product' (sản phẩm) hoặc 'farm' (thửa đất)."
                    ),
                )
            )

    # 4. KIỂM TRA ĐIỀU KIỆN KỊCH BẢN THẤT BẠI:
    # Nếu có BẤT KỲ dòng nào bị lỗi -> ROLLBACK TOÀN BỘ TRANSACTION & THROW HTTP EXCEPTION
    if errors:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": (
                    f"Tệp CSV có {len(errors)} lỗi dữ liệu. "
                    "Không có bản ghi nào được tạo trong cơ sở dữ liệu (đã rollback toàn bộ transaction)."
                ),
                "errors": [e.model_dump() for e in errors],
            },
        )

    # Nếu không có dòng dữ liệu nào được tìm thấy
    total_count = len(pending_products) + len(pending_farms)
    if total_count == 0:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Tệp CSV không chứa bất kỳ dòng dữ liệu sản phẩm hoặc thửa đất nào.",
                "errors": [{"row": 0, "column": "Tệp tin", "message": "Không tìm thấy dữ liệu hợp lệ để nhập."}],
            },
        )

    # 5. KỊCH BẢN THÀNH CÔNG: Lưu tất cả vào CSDL trong cùng 1 Transaction
    try:
        for p in pending_products:
            db.add(p)
        for f in pending_farms:
            db.add(f)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": f"Lỗi cơ sở dữ liệu khi lưu: {exc}",
                "errors": [{"row": 0, "column": "Cơ sở dữ liệu", "message": str(exc)}],
            },
        ) from exc

    return CsvImportResponse(
        status="success",
        message=f"Nhập thành công {total_count} dòng.",
        total_imported=total_count,
        farms_imported=len(pending_farms),
        products_imported=len(pending_products),
    )


@router.get(
    "/template-csv",
    summary="Tải về tệp CSV mẫu chuẩn",
    description="Trả về tệp CSV mẫu có sẵn dữ liệu mẫu cho Sản phẩm và Thửa đất.",
)
def download_csv_template() -> Response:
    """Trả về tệp tin CSV mẫu để người dùng tải xuống điền dữ liệu."""
    template_content = (
        "type,name,unit,description,location,area,owner\r\n"
        "product,Xoài Cát Chu Cao Lãnh,kg,Xoài cát chu chuẩn VietGAP đóng thùng,,,\r\n"
        "product,Sầu riêng Ri6 Bến Tre,kg,Sầu riêng cơm vàng hạt lép đạt chuẩn xuất khẩu,,,\r\n"
        'farm,Vườn Xoài Mỹ Xương 1,,,"Xã Mỹ Xương, Huyện Cao Lãnh, Tỉnh Đồng Tháp",3.5,Hợp tác xã Xoài Mỹ Xương\r\n'
        'farm,Vùng Canh Tác Sầu Riêng 2,,,"Xã Bình Hàng Tây, Huyện Cao Lãnh, Tỉnh Đồng Tháp",2.0,Hộ Nông Dân Nguyễn Văn An\r\n'
    )
    return Response(
        content=template_content.encode("utf-8-sig"),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="mau_nhap_san_pham_va_thua.csv"',
        },
    )
