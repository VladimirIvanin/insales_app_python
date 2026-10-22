-- 0001: Начальная схема (stores, installs, challenges, sessions)
-- Версия схемы: 1

CREATE TABLE IF NOT EXISTS stores (
    id           TEXT PRIMARY KEY,
    shop         TEXT UNIQUE NOT NULL,
    password     TEXT NOT NULL,
    title        TEXT DEFAULT '',
    generation   INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL DEFAULT (julianday('now'))
);

CREATE TABLE IF NOT EXISTS installs (
    candidate    TEXT PRIMARY KEY,
    store_id     TEXT NOT NULL,
    shop         TEXT NOT NULL,
    password     TEXT NOT NULL,
    generation   INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL DEFAULT (julianday('now'))
);

CREATE TABLE IF NOT EXISTS challenges (
    sid          TEXT PRIMARY KEY,
    store_id     TEXT NOT NULL,
    nonce        TEXT NOT NULL,
    expires      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    sid          TEXT PRIMARY KEY,
    store_id     TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    expires      REAL NOT NULL,
    generation   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_stores_shop ON stores(shop);
CREATE INDEX IF NOT EXISTS idx_installs_store ON installs(store_id);
CREATE INDEX IF NOT EXISTS idx_challenges_store ON challenges(store_id);
CREATE INDEX IF NOT EXISTS idx_sessions_store ON sessions(store_id);