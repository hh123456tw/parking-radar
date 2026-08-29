-- 停車場基本資料會隨官方靜態檔更新，不保存可重新計算的分數。
CREATE TABLE IF NOT EXISTS parking_lots (
    lot_id VARCHAR(32) PRIMARY KEY,
    lot_name VARCHAR(120) NOT NULL,
    district VARCHAR(20) NOT NULL,
    address VARCHAR(255) NOT NULL,
    operator_type VARCHAR(40) NOT NULL,
    total_spaces INT NOT NULL,
    fee_info TEXT,
    -- 官方費率規則原樣保存，供後續任務解析收費明細。
    fare_rules_json LONGTEXT NULL,
    facility_type VARCHAR(20) NULL,
    facility_source VARCHAR(20) NULL,
    metadata_checked_at DATETIME NULL,
    -- 官方文字目前可能超過 80 字元，保留餘裕避免匯入失敗。
    service_time VARCHAR(255),
    latitude DECIMAL(10, 7),
    longitude DECIMAL(10, 7),
    supports_realtime BOOLEAN NOT NULL DEFAULT FALSE,
    source_updated_at DATETIME NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_lots_district (district)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 每筆快照只保存官方格數與時間；負數特殊狀態不寫入此表。
CREATE TABLE IF NOT EXISTS parking_snapshots (
    snapshot_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    lot_id VARCHAR(32) NOT NULL,
    available_spaces INT NOT NULL,
    source_updated_at DATETIME NOT NULL,
    captured_at DATETIME NOT NULL,
    CONSTRAINT fk_snapshots_lot FOREIGN KEY (lot_id)
        REFERENCES parking_lots(lot_id),
    CONSTRAINT uq_lot_source_time UNIQUE (lot_id, source_updated_at),
    INDEX idx_snapshots_lot_captured (lot_id, captured_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 相同正規化地址只向 Nominatim 查詢一次。
CREATE TABLE IF NOT EXISTS geocode_cache (
    normalized_address VARCHAR(255) PRIMARY KEY,
    display_address VARCHAR(255) NOT NULL,
    latitude DECIMAL(10, 7) NOT NULL,
    longitude DECIMAL(10, 7) NOT NULL,
    cached_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 匿名分析事件：只存白名單欄位，不保存地址、對話、IP 或精確座標。
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

-- 每次查詢最多一筆生命週期摘要；原始文字 14 天後清空，其餘 90 天後刪除。
CREATE TABLE IF NOT EXISTS analytics_query_details (
    request_id CHAR(36) PRIMARY KEY,
    occurred_at DATETIME NOT NULL,
    anonymous_id_hash CHAR(64) NOT NULL,
    source VARCHAR(20) NOT NULL,
    query_mode VARCHAR(10) NOT NULL,
    raw_query_text VARCHAR(500) NULL,
    parsed_query_json JSON NULL,
    destination_label VARCHAR(255) NULL,
    district VARCHAR(20) NULL,
    arrival_time DATETIME NULL,
    intent VARCHAR(20) NULL,
    outcome_code VARCHAR(40) NOT NULL,
    error_stage VARCHAR(32) NULL,
    fallback_reason VARCHAR(80) NULL,
    data_status VARCHAR(20) NULL,
    result_count INT NOT NULL DEFAULT 0,
    location_choice_count TINYINT NOT NULL DEFAULT 0,
    parse_ms INT NULL, geocode_ms INT NULL, freshness_ms INT NULL,
    database_ms INT NULL, walking_ms INT NULL, total_ms INT NOT NULL,
    official_data_at DATETIME NULL,
    collected_at DATETIME NULL,
    feedback_code VARCHAR(24) NULL,
    INDEX idx_query_details_occurred (occurred_at),
    INDEX idx_query_details_district_occurred (district, occurred_at),
    INDEX idx_query_details_device_occurred (anonymous_id_hash, occurred_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 每次成功查詢最多三筆當時推薦快照，不事後 JOIN 最新停車資料。
CREATE TABLE IF NOT EXISTS analytics_recommendations (
    request_id CHAR(36) NOT NULL,
    rank_position TINYINT NOT NULL,
    occurred_at DATETIME NOT NULL,
    parking_lot_id VARCHAR(32) NOT NULL,
    lot_name VARCHAR(100) NOT NULL,
    recommendation_group VARCHAR(20) NOT NULL,
    available_spaces INT NULL,
    total_spaces INT NULL,
    pressure_label VARCHAR(20) NULL,
    decision_status VARCHAR(20) NULL,
    straight_distance_m INT NULL,
    walking_distance_m INT NULL,
    walking_minutes DECIMAL(8,2) NULL,
    distance_source VARCHAR(16) NOT NULL,
    hourly_fee_label VARCHAR(100) NULL,
    daily_cap_label VARCHAR(100) NULL,
    facility_type_label VARCHAR(40) NULL,
    navigation_clicked_at DATETIME NULL,
    PRIMARY KEY (request_id, rank_position),
    INDEX idx_recommendations_occurred (occurred_at),
    INDEX idx_recommendations_lot_occurred (parking_lot_id, occurred_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
