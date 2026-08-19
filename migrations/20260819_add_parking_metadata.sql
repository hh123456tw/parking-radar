-- 重複執行安全的遷移：僅在欄位不存在時才新增，避免手動重跑出錯。
SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'fare_rules_json') = 0,
  'ALTER TABLE parking_lots ADD COLUMN fare_rules_json LONGTEXT NULL AFTER fee_info',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'facility_type') = 0,
  'ALTER TABLE parking_lots ADD COLUMN facility_type VARCHAR(20) NULL AFTER fare_rules_json',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'facility_source') = 0,
  'ALTER TABLE parking_lots ADD COLUMN facility_source VARCHAR(20) NULL AFTER facility_type',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'metadata_checked_at') = 0,
  'ALTER TABLE parking_lots ADD COLUMN metadata_checked_at DATETIME NULL AFTER facility_source',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
