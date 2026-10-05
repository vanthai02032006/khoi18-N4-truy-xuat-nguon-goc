-- =============================================================================
-- AGRI-TRACE: Database Initialization & Append-Only Permission Setup (T-25 / SCRUM-41)
-- File: backend/scripts/init-db.sql
-- =============================================================================

\echo '--- [AGRI-TRACE] Initializing PostgreSQL Schema and Permissions ---'

-- 1. TẠO BẢNG DANH MỤC SẢN PHẨM TOÀN HỆ THỐNG (T-14 / SCRUM-30)
-- Lưu ý: Không có cột organization_id, name UNIQUE, unit ENUM chuẩn
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    unit VARCHAR(20) NOT NULL DEFAULT 'kg',
    description VARCHAR(500),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT chk_products_unit CHECK (unit IN ('kg', 'g', 'ton', 'liter', 'box', 'bottle', 'piece', 'bundle'))
);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);

-- 2. TẠO CÁC BẢNG CƠ BẢN (Farms, Batches, Users)
CREATE TABLE IF NOT EXISTS farms (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    location VARCHAR(255) NOT NULL,
    area DOUBLE PRECISION NOT NULL CHECK (area > 0),
    owner VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS batches (
    id SERIAL PRIMARY KEY,
    farm_id INTEGER NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
    product_name VARCHAR(255) NOT NULL,
    quantity DOUBLE PRECISION NOT NULL CHECK (quantity > 0),
    harvest_date DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batches_farm_id ON batches(farm_id);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password VARCHAR(64) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'farmer'
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

CREATE TABLE IF NOT EXISTS handovers (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    sender_org_id INTEGER NOT NULL,
    recipient_org_id INTEGER NOT NULL,
    recipient_org_name VARCHAR(255) NOT NULL,
    notes VARCHAR(500),
    status VARCHAR(50) DEFAULT 'COMPLETED' NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- 3. TẠO BẢNG SỰ KIỆN CHUỖI LẠNH & NHẬT KÝ HÀNH TRÌNH (T-25 / SCRUM-41 - BẢNG APPEND-ONLY)
CREATE TABLE IF NOT EXISTS batch_events (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    details VARCHAR(1000),
    temperature DOUBLE PRECISION,
    created_by VARCHAR(50) NOT NULL,
    timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    prev_hash VARCHAR(64),
    hash_code VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_batch_events_batch_id ON batch_events(batch_id);

-- 4. THIẾT LẬP TÀI KHOẢN CSDL RIÊNG CHO APP RUNTIME (T-25 / SCRUM-41)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agri_app_user') THEN
        CREATE USER agri_app_user WITH PASSWORD 'AppRuntimePass2026!';
        \echo 'Created user agri_app_user for App Runtime'
    END IF;
END$$;

-- 5. PHÂN QUYỀN TRUY CẬP SCHEMA VÀ DÃY TỰ TĂNG (SEQUENCES)
GRANT USAGE ON SCHEMA public TO agri_app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agri_app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO agri_app_user;

-- 6. CẤP QUYỀN CRUD THÔNG THƯỜNG TRÊN CÁC BẢNG QUẢN LÝ DỮ LIỆU
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE farms, batches, products, users, handovers TO agri_app_user;

-- 7. THIẾT LẬP RÀNG BUỘC APPEND-ONLY CHO BẢNG batch_events:
-- CHỈ CẤP QUYỀN SELECT VÀ INSERT CHO TÀI KHOẢN APP RUNTIME
GRANT SELECT, INSERT ON TABLE batch_events TO agri_app_user;
-- THU HỒI HOÀN TOÀN QUYỀN UPDATE VÀ DELETE ĐỐI VỚI APP RUNTIME
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE batch_events FROM agri_app_user;

-- 8. THIẾT LẬP CƠ CHẾ TRIGGER BẢO VỆ CHỐNG SỬA/XOÁ (DEFENSE-IN-DEPTH)
-- Đảm bảo nếu ai đó cố tình gọi UPDATE hoặc DELETE thì CSDL sẽ TỪ CHỐI NGAY LẬP TỨC
CREATE OR REPLACE FUNCTION fn_prevent_update_delete_batch_events()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Table batch_events is strictly Append-Only. UPDATE and DELETE operations are forbidden.'
        USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_batch_events_modification ON batch_events;
CREATE TRIGGER trg_prevent_batch_events_modification
BEFORE UPDATE OR DELETE ON batch_events
FOR EACH ROW
EXECUTE FUNCTION fn_prevent_update_delete_batch_events();

-- 9. NẠP DỮ LIỆU KHỞI TẠO MẪU (SEED DATA)
INSERT INTO products (name, unit, description) VALUES
    ('Xoài Cát Chu', 'kg', 'Xoài Cát Chu đặc sản Cao Lãnh đạt chuẩn VietGAP'),
    ('Sầu Riêng Ri6', 'kg', 'Sầu Riêng Ri6 cơm vàng hạt lép Chợ Lách'),
    ('Bưởi Da Xanh', 'kg', 'Bưởi Da Xanh Sông Xoài chuẩn xuất khẩu GlobalGAP'),
    ('Thanh Long Ruột Đỏ', 'kg', 'Thanh Long Ruột Đỏ Hàm Mỹ chất lượng cao'),
    ('Nhãn Lồng Hương Chi', 'kg', 'Nhãn Lồng Hưng Yên Hương Chi cùi dày giòn ngọt'),
    ('Chè Ô Long Mộc Châu', 'box', 'Chè Ô Long búp non hữu cơ Mộc Châu đóng hộp')
ON CONFLICT (name) DO NOTHING;

\echo '--- [AGRI-TRACE] Database Initialization and Append-Only setup completed successfully! ---'
