"""Tạo tệp PDF hồ sơ truy xuất nguồn gốc nông sản phục vụ cán bộ kiểm tra đính kèm biên bản.

Đặc tính kỹ thuật & Nội dung tài liệu:
1. Thông tin lô nông sản (Mã lô, tên sản phẩm, sản lượng, ngày thu hoạch, vùng trồng, chủ sở hữu, đơn vị giữ lô).
2. Dòng thời gian sự kiện (Audit Chain): từng mắt xích, người thực hiện, tổ chức, thời điểm, mã hash.
3. Phả hệ tổ tiên và hậu duệ (Lineage Tree):
   - Danh sách tổ tiên (Ancestors) duyệt ngược BFS.
   - Danh sách hậu duệ (Descendants) duyệt xuôi BFS.
4. Cảnh báo vi phạm chuỗi lạnh (Cold-chain Violations): các vi phạm nhiệt độ / độ trễ liên quan.
5. Lệnh kiểm tra / thu hồi nếu có (Recall Orders & status).
6. Kết quả kiểm tra toàn vẹn chuỗi hash:
   - Trạng thái toàn vẹn (HỢP LỆ / BỊ SỬA ĐỔI LÉN).
   - Thời điểm xuất hồ sơ (Export timestamp).
   - Hash cuối chuỗi (Final leaf hash).
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def build_traceability_dossier_pdf(
    batch_data: dict[str, Any],
    events_data: list[dict[str, Any]],
    ancestors: list[str],
    descendants: list[str],
    violations: list[dict[str, Any]],
    recall_orders: list[dict[str, Any]],
    integrity_status: bool,
    tampered_index: int | None,
    final_hash: str,
    inspector_name: str = "inspector",
) -> bytes:
    """Tạo tệp PDF hồ sơ truy xuất nguồn gốc trong bộ nhớ và trả về bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#065f46"),
        alignment=1,  # Center
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#475569"),
        alignment=1,
    )
    h2_style = ParagraphStyle(
        "H2",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=10,
        spaceAfter=4,
    )
    normal_style = ParagraphStyle(
        "RegularText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#1e293b"),
    )
    mono_style = ParagraphStyle(
        "MonoText",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#334155"),
    )
    badge_green = ParagraphStyle(
        "BadgeGreen",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#065f46"),
    )
    badge_red = ParagraphStyle(
        "BadgeRed",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#b91c1c"),
    )

    story = []
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. Header
    story.append(Paragraph("HỒ SƠ TRUY XUẤT NGUỒN GỐC & GIÁM SÁT CHUỖI LẠNH", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph("BIÊN BẢN KIỂM TRA TOÀN VẸN VÀ LỊCH SỬ HÀNH TRÌNH NÔNG SẢN", subtitle_style))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#059669"), spaceBefore=2, spaceAfter=10))

    # 2. Bảng thông tin xuất hồ sơ & chứng thực
    export_meta_data = [
        [
            Paragraph("<b>Thời điểm xuất hồ sơ:</b>", normal_style),
            Paragraph(now_str, mono_style),
            Paragraph("<b>Cán bộ lập biên bản:</b>", normal_style),
            Paragraph(inspector_name, normal_style),
        ],
        [
            Paragraph("<b>Mã băm cuối chuỗi:</b>", normal_style),
            Paragraph(final_hash[:32] + "..." if len(final_hash) > 32 else final_hash, mono_style),
            Paragraph("<b>Trạng thái toàn vẹn:</b>", normal_style),
            Paragraph("✓ HỢP LỆ (HASH-CHAIN VALID)" if integrity_status else "⚠ PHÁT HIỆN SAI LỆCH", badge_green if integrity_status else badge_red),
        ],
    ]
    meta_table = Table(export_meta_data, colWidths=[130, 150, 120, 120])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10))

    # 3. Thông tin lô nông sản
    story.append(Paragraph("1. THÔNG TIN LÔ NÔNG SẢN KIỂM TRA", h2_style))
    b_code = batch_data.get("batch_code") or f"BATCH-{batch_data.get('id')}"
    p_name = batch_data.get("product_name", "—")
    qty = f"{batch_data.get('quantity', 0.0):,.1f} kg"
    h_date = str(batch_data.get("harvest_date", "—"))
    farm_info = batch_data.get("farm", {})
    farm_name = farm_info.get("name", "—") if isinstance(farm_info, dict) else "—"
    holder = batch_data.get("current_holder_org", "—")

    batch_table_data = [
        [
            Paragraph("<b>Mã lô nông sản:</b>", normal_style),
            Paragraph(f"<b>{b_code}</b>", normal_style),
            Paragraph("<b>Tên sản phẩm:</b>", normal_style),
            Paragraph(p_name, normal_style),
        ],
        [
            Paragraph("<b>Khối lượng hiện tại:</b>", normal_style),
            Paragraph(qty, normal_style),
            Paragraph("<b>Ngày thu hoạch:</b>", normal_style),
            Paragraph(h_date, normal_style),
        ],
        [
            Paragraph("<b>Vùng trồng xuất xứ:</b>", normal_style),
            Paragraph(farm_name, normal_style),
            Paragraph("<b>Tổ chức đang giữ:</b>", normal_style),
            Paragraph(holder, normal_style),
        ],
    ]
    b_table = Table(batch_table_data, colWidths=[120, 140, 110, 150])
    b_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(b_table)
    story.append(Spacer(1, 10))

    # 4. Phả hệ tổ tiên và hậu duệ
    story.append(Paragraph("2. PHẢ HỆ TỔ TIÊN & HẬU DUỆ (LINEAGE TREE)", h2_style))
    anc_str = " → ".join(ancestors) if ancestors else "(Là mẻ thu hoạch gốc - Không có lô mẹ)"
    desc_str = ", ".join(descendants) if descendants else "(Lô chưa tách - Không có lô con)"

    lineage_data = [
        [
            Paragraph("<b>Tổ tiên (Ancestors):</b>", normal_style),
            Paragraph(anc_str, normal_style),
        ],
        [
            Paragraph("<b>Hậu duệ (Descendants):</b>", normal_style),
            Paragraph(desc_str, normal_style),
        ],
    ]
    lineage_table = Table(lineage_data, colWidths=[140, 380])
    lineage_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0fdf4")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbf7d0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(lineage_table)
    story.append(Spacer(1, 10))

    # 5. Dòng thời gian sự kiện (Audit Chain)
    story.append(Paragraph("3. DÒNG THỜI GIAN SỰ KIỆN (AUDIT TRAIL - CHUỖI HASH BẤT BIẾN)", h2_style))
    ev_header = [
        Paragraph("<b>Thời điểm</b>", normal_style),
        Paragraph("<b>Hành động</b>", normal_style),
        Paragraph("<b>Tổ chức / Nhân sự</b>", normal_style),
        Paragraph("<b>Mã băm sự kiện (Hash)</b>", normal_style),
    ]
    ev_rows = [ev_header]
    for ev in events_data:
        ev_ts = str(ev.get("timestamp", ""))[:19].replace("T", " ")
        ev_type = str(ev.get("event_type", ""))
        actor_org = f"{ev.get('organization', '')} ({ev.get('actor', '')})"
        h_short = str(ev.get("hash", ""))[:20] + "..."

        ev_rows.append([
            Paragraph(ev_ts, mono_style),
            Paragraph(f"<b>{ev_type}</b>", normal_style),
            Paragraph(actor_org, normal_style),
            Paragraph(h_short, mono_style),
        ])

    ev_table = Table(ev_rows, colWidths=[100, 110, 160, 150])
    ev_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(ev_table)
    story.append(Spacer(1, 10))

    # 6. Vi phạm chuỗi lạnh & Cảnh báo nhiệt độ
    story.append(Paragraph("4. GIÁM SÁT CHUỖI LẠNH & VI PHẠM ĐÃ GHI NHẬN", h2_style))
    if violations:
        viol_header = [
            Paragraph("<b>Mã Chuyến</b>", normal_style),
            Paragraph("<b>Nhiệt Độ</b>", normal_style),
            Paragraph("<b>Thời Gian Vượt</b>", normal_style),
            Paragraph("<b>Ngưỡng Áp Dụng</b>", normal_style),
            Paragraph("<b>Lý Do Vi Phạm</b>", normal_style),
        ]
        viol_rows = [viol_header]
        for v in violations:
            viol_rows.append([
                Paragraph(str(v.get("shipment_code", "")), mono_style),
                Paragraph(f"<font color='#dc2626'><b>{v.get('recorded_temperature', '')}°C</b></font>", normal_style),
                Paragraph(f"{v.get('duration_minutes', '')} phút", normal_style),
                Paragraph(f"[{v.get('applied_temp_min', '')}°C, {v.get('applied_temp_max', '')}°C]", mono_style),
                Paragraph(str(v.get("violation_reason", "")), normal_style),
            ])
        viol_table = Table(viol_rows, colWidths=[90, 70, 90, 110, 160])
        viol_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fee2e2")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#fca5a5")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(viol_table)
    else:
        story.append(Paragraph("✓ Không phát hiện vi phạm nhiệt độ chuỗi lạnh nào trong suốt hành trình.", normal_style))
    story.append(Spacer(1, 10))

    # 7. Lệnh kiểm tra / thu hồi nếu có
    story.append(Paragraph("5. LỆNH KIỂM TRA & THU HỒI LIÊN QUAN", h2_style))
    if recall_orders:
        ro_header = [
            Paragraph("<b>Mã Lệnh</b>", normal_style),
            Paragraph("<b>Tiêu Đề</b>", normal_style),
            Paragraph("<b>Lý Do</b>", normal_style),
            Paragraph("<b>Tiến Độ</b>", normal_style),
            Paragraph("<b>Trạng Thái</b>", normal_style),
        ]
        ro_rows = [ro_header]
        for ro in recall_orders:
            stt = "HOÀN TẤT" if ro.get("status") == "COMPLETED" else "ĐANG XỬ LÝ"
            ro_rows.append([
                Paragraph(str(ro.get("order_code", "")), mono_style),
                Paragraph(str(ro.get("title", "")), normal_style),
                Paragraph(str(ro.get("reason", "")), normal_style),
                Paragraph(str(ro.get("progress_ratio", "—")), normal_style),
                Paragraph(stt, badge_green if stt == "HOÀN TẤT" else badge_red),
            ])
        ro_table = Table(ro_rows, colWidths=[90, 130, 160, 60, 80])
        ro_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0e7ff")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c7d2fe")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(ro_table)
    else:
        story.append(Paragraph("✓ Lô hàng không có lệnh thu hồi hoặc cảnh báo khẩn cấp nào.", normal_style))
    story.append(Spacer(1, 14))

    # 8. Kết luận & Chữ ký xác nhận
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#94a3b8"), spaceBefore=4, spaceAfter=8))
    sign_data = [
        [
            Paragraph("<b>ĐẠI DIỆN ĐƠN VỊ LƯU GIỮ LÔ</b><br/><i>(Ký và ghi rõ họ tên)</i>", normal_style),
            Paragraph("<b>CÁN BỘ KIỂM TRA / LẬP BIÊN BẢN</b><br/><i>(Xác nhận tính toàn vẹn hồ sơ)</i>", normal_style),
        ],
        [
            Spacer(1, 30),
            Spacer(1, 30),
        ],
        [
            Paragraph(f"Đại diện: {holder}", normal_style),
            Paragraph(f"Cán bộ: {inspector_name}", normal_style),
        ],
    ]
    sign_table = Table(sign_data, colWidths=[260, 260])
    sign_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(sign_table)

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
