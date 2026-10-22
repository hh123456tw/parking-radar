-- 支援依 captured_at 分批刪除八天以前的停車快照。
-- 重複執行安全：僅在索引不存在時才新增，避免手動重跑出錯。
SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_snapshots'
     AND INDEX_NAME = 'idx_snapshots_captured_lot') = 0,
  'ALTER TABLE parking_snapshots ADD INDEX idx_snapshots_captured_lot (captured_at, lot_id)',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
