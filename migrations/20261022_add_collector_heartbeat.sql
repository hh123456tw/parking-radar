-- 重複執行安全的遷移：Collector 單列心跳表，官方資料凍結時仍可判斷 Collector 存活。
CREATE TABLE IF NOT EXISTS collector_heartbeat (
    id TINYINT PRIMARY KEY,
    last_success_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
