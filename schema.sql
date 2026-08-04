-- 停車場基本資料會隨官方靜態檔更新，不保存可重新計算的分數。
CREATE TABLE IF NOT EXISTS parking_lots (
    lot_id VARCHAR(32) PRIMARY KEY,
    lot_name VARCHAR(120) NOT NULL,
    district VARCHAR(20) NOT NULL,
    address VARCHAR(255) NOT NULL,
    operator_type VARCHAR(40) NOT NULL,
    total_spaces INT NOT NULL,
    fee_info TEXT,
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
