-- 0003: Таблицы webhook_subscriptions и webhook_inbox
-- Версия схемы: 3

CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id        TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    generation      INTEGER NOT NULL,
    topic           TEXT NOT NULL,
    insales_id      INTEGER,
    callback_secret TEXT NOT NULL,
    address         TEXT NOT NULL,
    format_type     TEXT NOT NULL DEFAULT 'json',
    state           TEXT NOT NULL DEFAULT 'active',
    created_at      REAL NOT NULL DEFAULT (julianday('now')),
    updated_at      REAL NOT NULL DEFAULT (julianday('now')),
    UNIQUE(store_id, topic)
);

CREATE TABLE IF NOT EXISTS webhook_inbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id        TEXT NOT NULL,
    generation      INTEGER NOT NULL,
    topic           TEXT NOT NULL,
    payload         TEXT NOT NULL,
    headers         TEXT DEFAULT '{}',
    state           TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 5,
    next_attempt_at REAL,
    error           TEXT,
    locked_at       REAL,
    locked_by       TEXT,
    created_at      REAL NOT NULL DEFAULT (julianday('now')),
    updated_at      REAL NOT NULL DEFAULT (julianday('now'))
);

CREATE INDEX IF NOT EXISTS idx_webhook_subscriptions_store
    ON webhook_subscriptions(store_id);
CREATE INDEX IF NOT EXISTS idx_webhook_inbox_state
    ON webhook_inbox(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_webhook_inbox_store
    ON webhook_inbox(store_id);