-- =============================================================================
-- Migration: 002_batch_events_append_only.sql
-- Mục đích: Bảng CHỈ THÊM (append-only) cho nhật ký hành trình lô nông sản
--           + thu hồi quyền UPDATE/DELETE của tài khoản ứng dụng (production).
--
-- PHẠM VI: file này dành cho **PostgreSQL (production)**. Ở môi trường local/CI
-- dùng SQLite - SQLite không có role/GRANT nên quy ước tương đương được thực thi
-- bằng `app/append_only.py` (authorizer + trigger). Cả hai đường đều bảo vệ cùng
-- một quy ước: tài khoản ứng dụng CHỈ được SELECT và INSERT trên batch_events.
--
-- Cách chạy (bằng tài khoản migration/admin có quyền DDL):
--   psql "$DATABASE_URL_ADMIN" -f backend/migrations/002_batch_events_append_only.sql
-- =============================================================================

-- +goose Up
-- +migrate Up
-- ------------------------------------------------------------- UP MIGRATION ---

-- 1. Bảng nhật ký bất biến: hành trình lô nông sản & dữ liệu chuỗi lạnh.
CREATE TABLE IF NOT EXISTS batch_events (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    event_type VARCHAR(50) NOT NULL,   -- HARVEST, COLD_STORAGE, HANDOVER, MERGE, INSPECTION
    details VARCHAR(1000),
    temperature DOUBLE PRECISION,      -- Nhiệt độ chuỗi lạnh (°C)
    created_by VARCHAR(50) NOT NULL,
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    prev_hash VARCHAR(64),             -- Băm SHA-256 của bản ghi liền trước
    hash_code VARCHAR(64)              -- Băm SHA-256 của chính bản ghi này
);

CREATE INDEX IF NOT EXISTS idx_batch_events_batch_id ON batch_events(batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_events_timestamp ON batch_events(timestamp);

-- 2. Tài khoản CSDL riêng cho ứng dụng (App Runtime) nếu chưa có.
--    Mật khẩu KHÔNG đặt trong file này: truyền qua biến môi trường của công cụ
--    migration và lưu trong secret manager của môi trường triển khai.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agri_app_user') THEN
        CREATE ROLE agri_app_user LOGIN;
    END IF;
END$$;

GRANT USAGE ON SCHEMA public TO agri_app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agri_app_user;

-- 3. Các bảng nghiệp vụ khác: tài khoản ứng dụng được CRUD bình thường.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE farms, batches, users TO agri_app_user;

-- 4. LỚP BẢO VỆ 1 - QUYỀN TÀI KHOẢN ỨNG DỤNG:
--    chỉ cấp SELECT + INSERT, thu hồi hoàn toàn UPDATE/DELETE/TRUNCATE.
GRANT SELECT, INSERT ON TABLE batch_events TO agri_app_user;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE batch_events FROM agri_app_user;

-- 5. LỚP BẢO VỆ 2 - TRIGGER (van an toàn, chặn cả tài khoản toàn quyền).
CREATE OR REPLACE FUNCTION fn_prevent_update_delete_batch_events()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'CSDL TU CHOI: bang batch_events la bang chi them (append-only) - nghiem cam moi thao tac UPDATE/DELETE.'
        USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_update_batch_events ON batch_events;
CREATE TRIGGER trg_prevent_update_batch_events
BEFORE UPDATE ON batch_events
FOR EACH ROW
EXECUTE FUNCTION fn_prevent_update_delete_batch_events();

DROP TRIGGER IF EXISTS trg_prevent_delete_batch_events ON batch_events;
CREATE TRIGGER trg_prevent_delete_batch_events
BEFORE DELETE ON batch_events
FOR EACH ROW
EXECUTE FUNCTION fn_prevent_update_delete_batch_events();

COMMENT ON TABLE batch_events IS
    'Nhat ky bat bien hanh trinh lo nong san & giam sat chuoi lanh. Quy uoc BANG CHI THEM: tai khoan ung dung chi duoc SELECT va INSERT.';

-- +goose Down
-- +migrate Down
-- ----------------------------------------------------------- DOWN MIGRATION ---

DROP TRIGGER IF EXISTS trg_prevent_update_batch_events ON batch_events;
DROP TRIGGER IF EXISTS trg_prevent_delete_batch_events ON batch_events;
DROP FUNCTION IF EXISTS fn_prevent_update_delete_batch_events();

REVOKE ALL PRIVILEGES ON TABLE batch_events FROM agri_app_user;

DROP TABLE IF EXISTS batch_events;
