-- 0004: Таблица tombstones для отменённых установок
-- Версия схемы: 4
-- Защищает activate_install от активации кандидата,
-- который был отменён после добавления (гонка с uninstall).

CREATE TABLE IF NOT EXISTS store_tombstones (
    store_id     TEXT NOT NULL,
    generation   INTEGER NOT NULL,
    created_at   REAL NOT NULL DEFAULT (julianday('now')),
    PRIMARY KEY (store_id, generation)
);

CREATE INDEX IF NOT EXISTS idx_tombstones_store
    ON store_tombstones(store_id);