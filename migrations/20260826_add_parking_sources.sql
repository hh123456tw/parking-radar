-- 重複執行安全的城市/來源遷移：先查 INFORMATION_SCHEMA 再補欄位，
-- 補完既有列後才收緊 NOT NULL，最後只在唯一鍵不存在時建立。
SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'city') = 0,
  'ALTER TABLE parking_lots ADD COLUMN city VARCHAR(20) NULL AFTER district',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'source') = 0,
  'ALTER TABLE parking_lots ADD COLUMN source VARCHAR(20) NULL AFTER city',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'source_lot_id') = 0,
  'ALTER TABLE parking_lots ADD COLUMN source_lot_id VARCHAR(64) NULL AFTER source',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 既有列全部視為臺北官方資料，新欄位才有值可回填。
UPDATE parking_lots
SET city = '臺北市', source = 'taipei', source_lot_id = lot_id
WHERE city IS NULL OR source IS NULL OR source_lot_id IS NULL;

SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'city' AND IS_NULLABLE = 'YES') > 0,
  'ALTER TABLE parking_lots MODIFY city VARCHAR(20) NOT NULL',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'source' AND IS_NULLABLE = 'YES') > 0,
  'ALTER TABLE parking_lots MODIFY source VARCHAR(20) NOT NULL',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'source_lot_id' AND IS_NULLABLE = 'YES') > 0,
  'ALTER TABLE parking_lots MODIFY source_lot_id VARCHAR(64) NOT NULL',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 同來源官方 ID 必須唯一，避免跨城市來源衝突。
SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND INDEX_NAME = 'uq_lots_source_id') = 0,
  'ALTER TABLE parking_lots ADD UNIQUE KEY uq_lots_source_id (source, source_lot_id)',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

