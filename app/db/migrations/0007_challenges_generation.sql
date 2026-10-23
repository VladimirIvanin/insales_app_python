-- 0007: Добавлен generation в challenges
-- Версия схемы: 7
-- Привязывает challenge к поколению магазина, чтобы старый challenge
-- не мог получить сессию нового поколения (защита от B2 / backend-2).

ALTER TABLE challenges ADD COLUMN generation INTEGER NOT NULL DEFAULT 0;