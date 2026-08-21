# Реестр обработки персональных данных — prelaunch draft (Пульт OS)

Статус: **DRAFT / INTERNAL**. Оператор: ИП Муратков Иналь Олегович. Реквизиты (ИНН, ОГРНИП, адрес) подставляются локально из `[REQUIRES INAL — INSERT LOCALLY BEFORE PUBLICATION]`; в Git не записываются.

База инвентаризации: `origin/master` `5584cd6`. Доказательства `file:line` собраны на этой базе и сведены в `source-evidence.md`. Правовые основания в этом реестре обозначены как **REQUIRES LAWYER**, пока не подтверждены профильным юристом РФ.

## 1. Фактически обнаруженные категории данных

| Категория | Поля (факт по коду) | Источник | Назначение | Правовое основание | Статус реализации | Доказательство |
|---|---|---|---|---|---|---|
| Учётная запись | email, имя, `hashed_password` (bcrypt), `created_at`, `plan`, `is_verified`, `verification_token`, `subscription_end_date` | регистрация | аккаунт, аутентификация, обслуживание | REQUIRES LAWYER (исполнение соглашения) | IMPLEMENTED | `models/user.py:11-51` |
| Безопасность аккаунта | `registered_ip` (String 45), `token_version` (ревокация сессий), `reset_token`+`reset_token_expires` (только SHA-256 digest), MFA-secret (Fernet), `last_totp_step` (anti-replay) | запрос/пользователь | защита аккаунта, расследование инцидентов | REQUIRES LAWYER (законный интерес в безопасности) | IMPLEMENTED | `models/user.py:24-48`, `models/mfa_secret.py:11-23`, `services/mfa_crypto.py:16-30` |
| Реферальные данные | `referral_code`, `referred_by_id`, `was_referrer`, `was_referred` | пользователь/система | реферальная программа | REQUIRES LAWYER | код есть; коммерческий запуск не подтверждён | `models/user.py:40-46` |
| Telegram | `telegram_chat_id`, `TelegramSettings` (~20 полей предпочтений), `TelegramNotificationLog` (лог доставки/дедуп) | пользователь/Telegram | уведомления по выбору пользователя | REQUIRES LAWYER (согласие/действие пользователя) | интеграция wired; фактически OFF (пустой токен + opt-in chat_id) | `models/user.py:37`, `models/telegram_settings.py:6-40`, `models/telegram_notification_log.py:7-18`, `services/telegram.py:11-19` |
| Marketplace identity | `MarketplaceAccount.external_account_id`, `MarketplaceStore.external_store_id`, `Product.external_product_id`, `ProductPlacement.external_offer_id`, Ozon `ozon_client_id` | пользователь/API | привязка кабинета/магазина/товара | REQUIRES LAWYER | IMPLEMENTED (частично) | `models/marketplace_account.py:49-66`, `models/marketplace_store.py:41-52`, `models/product.py:11-27`, `models/marketplace_connection.py:40` |
| Marketplace credentials | `secret_enc` (Fernet ciphertext, никогда не plaintext), scope (feedbacks/prices/advert/content/stocks/promotions), `credential_fingerprint` (HMAC-SHA256, WB), `verification_status` | пользователь/маркетплейс | импорт и разрешённые операции | REQUIRES LAWYER | хранение IMPLEMENTED; execution-флаги OFF | `models/api_credential.py:39-49`, `services/marketplace/credential_vault.py:22-96` |
| Товарные данные | `sku`, `title`, `price`, `stock`, `rating`, `reviews_count`, `external_product_id` | CSV/API | аналитика магазина | REQUIRES LAWYER | IMPLEMENTED | `models/imported_product.py:14-19`, `models/product.py:11-27` |
| Финансовые/операционные данные | revenue, commission, logistics, ad_spend, net_profit, quantity, returns_qty+reason, margin | CSV/API | финансовая/операционная аналитика | REQUIRES LAWYER | IMPLEMENTED | `models/imported_finance.py:17-22`, `models/financial_snapshot.py:14-19`, `models/imported_return.py:21-24` |
| Отзывы | `review_text`, `author`, `rating`, `response_text`, `external_review_id`, `safety_category` | API маркетплейса | подготовка и публикация ответов | REQUIRES LAWYER | модель IMPLEMENTED; автопубликация OFF (3 gate) | `models/review_response.py:13-34`, `tasks/auto_publish_reviews.py:106-138` |
| Решения и аналитика | рекомендации, сигналы, оценки, результаты применения | система из данных магазина | Advisory MVP | REQUIRES LAWYER | IMPLEMENTED | сигнальные таблицы `models/*_signal.py` |
| Уведомления (in-product) | `type`, `title`, `message` (≤1000), `is_read`, `created_at` | система | сообщения внутри продукта | REQUIRES LAWYER | IMPLEMENTED | `models/notification.py:7-18` |
| Платежи | `yookassa_payment_id`, `amount`, `tariff`, `plan`, `status`, timestamps | ЮKassa | оплата и учёт | REQUIRES LAWYER (обязанность по учёту) | backend IMPLEMENTED; пользовательский платный путь ЗАКРЫТ | `models/payment.py:8-21`, `routers/payments.py`, `frontend/app/checkout/page.tsx:8-27` |
| OAuth | provider (google/apple/yandex), `provider_user_id`, `email`, `name` | Google/Apple/Яндекс | альтернативный вход | HOLD | модель есть; router DISABLED (403), не смонтирован | `models/oauth_account.py:10-16`, `routers/oauth.py:27-30` |
| Данные в браузере | см. `cookie-notice.DRAFT.md` (session cookie, remember-email, язык, тема, режим кабинета, onboarding, consent choice, локальные черновики) | браузер пользователя | работа интерфейса | REQUIRES LAWYER | локально в браузере | `frontend/lib/session.ts`, `frontend/components/CookieBanner.tsx` |

## 2. Что НЕ собирается текущим production-кодом

- Поведенческий сбор событий, visitor ID, UTM/referrer, CTA telemetry — удалён и охраняется тестом `frontend/tests/noBehavioralEvents.test.ts`.
- Данные банковских карт ПУЛЬТу не поступают: их принимает платёжный провайдер (`frontend/app/privacy/page.tsx:41,75`).
- Пароль в открытом виде не хранится: bcrypt (`routers/auth.py:47-48`).
- Marketplace API tokens хранятся только зашифрованными (Fernet); расшифровка только в момент диспетчеризации, не логируется (`services/marketplace/credential_vault.py:4-6,72-77`).
- Reset-токен хранится только как SHA-256 digest, не в открытом виде (`routers/auth.py:495-499`).
- Пер-попыточная таблица аудита с plaintext email+IP удалена (не было читателей); логи пишут только `user=<id>`, без email/IP/токенов (`routers/auth.py:102-105,164,209`).

## 3. Субъекты данных

1. Пользователи Пульт OS — ИП, представители и сотрудники продавцов.
2. Представители контрагентов пользователя, если их сведения попали в загруженные данные.
3. Авторы отзывов на маркетплейсах — текст отзыва, имя/псевдоним и связанные публичные сведения.
4. Получатели поддержки и уведомлений.
5. Рефералы и приглашающие пользователи.

Пользователь гарантирует законное основание для загрузки данных третьих лиц. Это не освобождает оператора Пульт OS от собственных обязанностей по 152-ФЗ.

## 4. Внешние получатели и обработчики

| Получатель | Данные/операция | Статус | Доказательство |
|---|---|---|---|
| Selectel | сервер, БД, резервные копии, S3 | **PLANNED / CONTRACT PENDING** (сервер не заказан, поручение не подписано, регионы не финализированы) | `docs/backup-restore-policy.md:19-21,56`; env-плейсхолдеры `docker-compose.backup.yml:33-40` |
| SMTP-провайдер | имя, email, verification/reset link | **PLANNED** (в dev письмо логируется; provider не зафиксирован; в production обязателен) | `config.py:58-63,308-309`, `services/email.py:25-50` |
| Яндекс 360 (почта) | адреса поддержки/ПДн на pult-os.ru | **PLANNED / MAIL GATE PENDING** | настраивается Иналом отдельно |
| Telegram | chat ID и текст выбранных уведомлений | только по явному подключению; фактически OFF (пустой токен) | `services/telegram.py:11-19`, `config.py:41` |
| Wildberries/Ozon/Яндекс Маркет | токены, cabinet IDs, запросы, разрешённые изменения | по подключению и scopes; ingest OFF (`api_data_sync_enabled=False`), execution OFF (`automation_enabled=False`) | `config.py:80-98,107` |
| ЮKassa | сумма, назначение, идентификатор платежа | не включать до коммерческого launch; env-ключи не заданы | `config.py:71-73`, `payments.py:190` |
| Google/Apple/Яндекс OAuth | внешний ID, email, имя | **HOLD** до проверки трансграничной передачи; router DISABLED | `routers/oauth.py:27-30` |
| Sentry | телеметрия ошибок (scrubbed, без тел запросов/PII) | configured-but-off (DSN пуст → no-op) | `services/sentry_setup.py:100-115`, `config.py:68` |

## 5. Retention — честное текущее состояние

**Только один срок фактически enforced в runtime — 1-часовой TTL orphan CSV. Всё остальное — DOCUMENTED-ONLY, UNKNOWN или feature-flagged OFF.**

| Данные | Текущая реализация | Статус | Доказательство |
|---|---|---|---|
| Неподтверждённые CSV uploads | удаляются после импорта; orphan sweep каждые 15 мин, TTL = 3600 с (1 ч); wired без флага | **IMPLEMENTED (active)** | `routers/csv_import.py:51-55,730-766`, `tasks/uploads_cleanup.py:29,84`, `scheduler.py:449,704-718` |
| Marketplace observations | sweep 180 дней (resolved) / 30 дней (unassigned), latest-of-series хранится вечно | **feature-flagged OFF** (`observation_retention_enabled=False`, dry-run=True) — не обещать как работающее | `services/marketplace/retention/observation_sweep.py:43-44`, `config.py:112,116`, `scheduler.py:463-476` |
| Security/login logs | публичная privacy-страница обещает «не более 90 дней», но в коде НЕ найдено sweep, удаляющего auth-логи по 90 дней | **UNKNOWN / промис без реализации** — устранить (реализовать TTL или убрать обещание) | claim `frontend/app/privacy/page.tsx:116`; sweep не найден |
| Аккаунт | soft-delete: `deleted_at` set, строка НЕ стирается, email можно переиспользовать, при удалении сносится папка CSV этого продавца | **soft-delete only; hard-delete НЕ реализован** — не выдавать за полное стирание | `routers/referrals.py:253-274` |
| Полное удаление по запросу | privacy обещает исполнение «в течение 30 дней» — это ручной SLA на email, не автоматическая джоба | **DOCUMENTED-ONLY (manual SLA)** | claim `frontend/app/privacy/page.tsx:113` |
| Credentials | остаются до отзыва/удаления связи; `expires_at` хранится, но НЕ enforced (нет ротации/экспирации) | **UNKNOWN (hard-delete не доказан)** | `models/api_credential.py:26,43` |
| Платёжные документы | срок в коде не определён | **UNKNOWN** — установить по бухгалтерским/налоговым требованиям | `models/payment.py` |
| Резервные копии | production-сроки не определены; scheduler dormant; Object Lock не enabled | **UNKNOWN / dormant** | `docs/backup-restore-policy.md:13-15,45-47,107-109` |

## 6. Права субъекта — механизмы (честно)

| Право | Механизм в продукте | Статус |
|---|---|---|
| Получить сведения об обработке | нет self-service; через обращение на почту | UNKNOWN (после Mail Gate) |
| Исправление | нет отдельного механизма исправления ПДн; часть настроек редактируется в аккаунте | PARTIAL |
| Выгрузка/переносимость | endpoint экспорта ПДн не найден | UNKNOWN |
| Блокирование | как отдельная операция не реализовано | UNKNOWN |
| Удаление | `DELETE /account` = деактивация (soft-delete), не стирание | PARTIAL — hard-delete/anonymization не реализованы |
| Отзыв согласия | серверная запись согласия отсутствует, поэтому и доказуемого отзыва нет | UNKNOWN — зависит от server-side consent |

## 7. Открытые решения (сведены в `launch-legal-checklist.md`)

- фактический УЗ ПДн и модель угроз (не заявлять конкретный УЗ до определения);
- правовые основания по каждой цели, а не одно общее согласие;
- полный перечень данных третьих лиц из marketplace API;
- срок ответа Selectel об инциденте и подписанное поручение;
- SMTP-провайдер и география обработки;
- hard-delete/anonymization и удаление из backup после Object Lock;
- версия и серверная фиксация каждого согласия;
- порядок экспорта, исправления, блокирования и уничтожения по запросу субъекта;
- порядок уведомления Роскомнадзора (о начале обработки и об инцидентах);
- 2 CSV-файла, отслеживаемых в Git (`backend/uploads/imports/...`) — устранить до launch (это уже загруженные данные, они не должны храниться в репозитории).
