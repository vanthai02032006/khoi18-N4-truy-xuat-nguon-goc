-- =============================================================================
-- Migration: 002_create_batch_events_and_permissions.sql (T-25 / SCRUM-41)
-- Mô tả: Tạo bảng batch_events và cấu hình phân quyền Append-Only cho App Runtime
-- =============================================================================

-- +goose Up
-- +migrate Up
-- ------------------------------------------------------------- UP MIGRATION ---

-- 1. Tạo bảng sự kiện hành trình lô nông sản & chuỗi lạnh (batch_events)
CREATE TABLE IF NOT EXISTS batch_events (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL, -- HARVEST, COLD_STORAGE, HANDOVER, MERGE, INSPECTION
    details VARCHAR(1000),
    temperature DOUBLE PRECISION,     -- Nhiệt độ chuỗi lạnh (°C)
    created_by VARCHAR(50) NOT NULL,
    timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    prev_hash VARCHAR(64),            -- Hash chuỗi chống sửa lén (Spike K-01)
    hash_code VARCHAR(64)             -- Hash của sự kiện hiện tại
);

CREATE INDEX IF NOT EXISTS idx_batch_events_batch_id ON batch_events(batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_events_timestamp ON batch_events(timestamp);

-- 2. Tạo tài khoản CSDL riêng cho App Runtime nếu chưa có
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agri_app_user') THEN
        CREATE USER agri_app_user WITH PASSWORD 'AppRuntimePass2026!';
    END IF;
END$$;

-- 3. Cấp quyền cơ bản truy cập Schema
GRANT USAGE ON SCHEMA public TO agri_app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agri_app_user;

-- 4. Cấp quyền CRUD thông thường cho các bảng nghiệp vụ khác
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE farms, batches, products, users, handovers TO agri_app_user;

-- 5. CẤU HÌNH QUYỀN APPEND-ONLY CHO BẢNG batch_events:
-- CHỈ CẤP SELECT VÀ INSERT, THU HỒI HOÀN TOÀN UPDATE VÀ DELETE
GRANT SELECT, INSERT ON TABLE batch_events TO agri_app_user;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE batch_events FROM agri_app_user;

-- 6. TẠO DATABASE TRIGGER ĐẢM BẢO TÍNH BẤT BIẾN (DEFENSE-IN-DEPTH)
-- Ngăn chặn tuyệt đối mọi hành vi UPDATE hoặc DELETE bảng batch_events
CREATE OR REPLACE FUNCTION fn_prevent_update_delete_batch_events()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Table batch_events is strictly Append-Only. UPDATE and DELETE operations are forbidden by security policy.'
        USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_batch_events_modification ON batch_events;
CREATE TRIGGER trg_prevent_batch_events_modification
BEFORE UPDATE OR DELETE ON batch_events
FOR EACH ROW
EXECUTE FUNCTION fn_prevent_update_delete_batch_events();

COMMENT ON TABLE batch_events IS 'Nhật ký bất biến chuỗi cung ứng & giám sát chuỗi lạnh. Tài khoản App Runtime chỉ có quyền SELECT và INSERT (Append-Only).';


-- +goose Down
-- +migrate Down
-- ----------------------------------------------------------- DOWN MIGRATION ---

DROP TRIGGER IF EXISTS trg_prevent_batch_events_modification ON batch_events;
DROP FUNCTION IF EXISTS fn_prevent_update_delete_batch_events();
REVOKE ALL PRIVILEGES ON TABLE batch_events FROM agri_app_user;
DROP TABLE IF EXISTS batch_events;
