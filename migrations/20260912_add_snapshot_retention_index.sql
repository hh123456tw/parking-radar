-- 支援依 captured_at 分批刪除八天以前的停車快照。
ALTER TABLE parking_snapshots
    ADD INDEX idx_snapshots_captured_lot (captured_at, lot_id);
