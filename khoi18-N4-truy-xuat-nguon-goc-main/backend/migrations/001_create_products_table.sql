-- =============================================================================
-- Migration: 001_create_products_table.sql (T-14 / SCRUM-30)
-- Mô tả: Tạo bảng products danh mục dùng chung toàn hệ thống (không có organization_id)
-- =============================================================================

-- +goose Up
-- +migrate Up
-- ------------------------------------------------------------- UP MIGRATION ---

-- 1. Tạo kiểu ENUM cho đơn vị tính chuẩn (PostgreSQL) nếu chưa có
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'product_unit_enum') THEN
        CREATE TYPE product_unit_enum AS ENUM ('kg', 'g', 'ton', 'liter', 'box', 'bottle', 'piece', 'bundle');
    END IF;
END$$;

-- 2. Tạo bảng products:
-- - KHÔNG có cột organization_id (danh mục toàn hệ thống)
-- - Ràng buộc UNIQUE trên name chống trùng lặp
-- - Kiểu ENUM cho unit
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    unit VARCHAR(20) NOT NULL DEFAULT 'kg',
    description VARCHAR(500),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT chk_products_unit CHECK (unit IN ('kg', 'g', 'ton', 'liter', 'box', 'bottle', 'piece', 'bundle'))
);

-- Index tăng tốc tìm kiếm theo tên
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);

-- Comment làm rõ thiết kế kiến trúc
COMMENT ON TABLE products IS 'Bảng danh mục sản phẩm chuẩn dùng chung toàn hệ thống. KHÔNG có organization_id và KHÔNG áp dụng Tenant Scope filter.';
COMMENT ON COLUMN products.name IS 'Tên sản phẩm duy nhất toàn hệ thống (UNIQUE)';
COMMENT ON COLUMN products.unit IS 'Đơn vị tính chuẩn ENUM (kg, g, ton, liter, box, bottle, piece, bundle)';

-- +goose Down
-- +migrate Down
-- ----------------------------------------------------------- DOWN MIGRATION ---

DROP TABLE IF EXISTS products;
DROP TYPE IF EXISTS product_unit_enum;
