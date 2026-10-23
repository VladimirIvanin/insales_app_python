CREATE TABLE IF NOT EXISTS generation_counter (
    -- Служебная таблица для атомарного резервирования поколений.
    -- Хранит одну строку с последним выданным generation.
    -- next_generation() вставляет/обновляет эту строку в write-транзакции,
    -- гарантируя монотонность даже при вызовах в пределах одной миллисекунды.
    last_generation INTEGER NOT NULL
);

-- Инициализируем строку, если таблица только что создана
INSERT OR IGNORE INTO generation_counter (rowid, last_generation)
VALUES (1, 0);