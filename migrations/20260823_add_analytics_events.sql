-- 新增單一匿名分析事件表；重複執行安全，與 schema.sql 定義一致。
CREATE TABLE IF NOT EXISTS analytics_events (
    event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_type VARCHAR(32) NOT NULL,
    occurred_at DATETIME NOT NULL,
    request_id CHAR(36) NULL,
    anonymous_id_hash CHAR(64) NOT NULL,
    district VARCHAR(20) NULL,
    area_bucket VARCHAR(32) NULL,
    place_type VARCHAR(32) NULL,
    query_mode VARCHAR(10) NULL,
    outcome_code VARCHAR(40) NULL,
    duration_ms INT NULL,
    result_count INT NULL,
    clicked_rank TINYINT NULL,
    parking_lot_id VARCHAR(32) NULL,
    walking_minutes DECIMAL(8, 2) NULL,
    availability_bucket VARCHAR(16) NULL,
    source VARCHAR(20) NOT NULL,
    INDEX idx_analytics_occurred (occurred_at),
    INDEX idx_analytics_type_occurred (event_type, occurred_at),
    INDEX idx_analytics_device_occurred (anonymous_id_hash, occurred_at),
    -- 同一 request 每個事件型態只接受第一筆，導航點擊以此去重。
    UNIQUE KEY uq_analytics_request_event (request_id, event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
