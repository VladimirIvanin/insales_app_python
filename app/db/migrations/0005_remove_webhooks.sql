-- 0005: Удаление неиспользуемых таблиц webhook
-- Версия схемы: 5
-- Webhook-функционал удалён из кода приложения.
-- Таблицы webhook_subscriptions и webhook_inbox больше не используются.
-- Код приложения не обращается к ним, worker'ы удалены.

DROP TABLE IF EXISTS webhook_inbox;
DROP TABLE IF EXISTS webhook_subscriptions;