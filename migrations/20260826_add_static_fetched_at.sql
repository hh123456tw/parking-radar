-- 重複執行安全的靜態抓取標記遷移：先查 INFORMATION_SCHEMA 再補欄位。
-- 此標記只由靜態資料成功抓取後寫入，動態-only 週期不得改寫，
-- 避免 24 小時靜態刷新門檻因動態 upsert 被永久延後。
SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'static_fetched_at') = 0,
  'ALTER TABLE parking_lots ADD COLUMN static_fetched_at DATETIME NULL AFTER source_updated_at',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
