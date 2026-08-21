# Source evidence — карта «документ → код» и нормативные источники

Статус: **DRAFT / INTERNAL**. База: `origin/master` `5584cd6726cf9ec19ffd6523a614372b1368b304`. Собрано офлайн, по локальному репозиторию.

Назначение: каждое фактическое утверждение в legal-пакете должно опираться на доказательство `file:line`, а каждый правовой вывод — на первичный источник или на пометку REQUIRES RUSSIAN COUNSEL REVIEW. Реальные пользовательские данные и секреты сюда не выносятся.

## A. Доказательства в коде (`file:line`)

| Утверждение в документах | Доказательство | Статус |
|---|---|---|
| Пароль хранится как bcrypt-хэш | `backend/routers/auth.py:47-48,51-52` | IMPLEMENTED |
| Сессия — JWT в HttpOnly cookie, ревокация через `token_version` | `backend/routers/auth.py:74-80`, `backend/dependencies.py:39-58`, `backend/models/user.py:34` | IMPLEMENTED |
| Session cookie `__Host-pult_session`/`pult_session_dev`, HttpOnly | `backend/auth_cookie.py:30-31,55-78` | IMPLEMENTED |
| MFA-secret зашифрован (Fernet), anti-replay `last_totp_step` | `backend/models/mfa_secret.py:11-23`, `backend/services/mfa_crypto.py:16-30` | IMPLEMENTED |
| Reset-токен хранится только как SHA-256 digest | `backend/routers/auth.py:495-499,531-546` | IMPLEMENTED |
| Marketplace-токены только зашифрованы (Fernet), не логируются | `backend/models/api_credential.py:41`, `backend/services/marketplace/credential_vault.py:4-6,22-77` | IMPLEMENTED |
| `expires_at` у credential хранится, но НЕ enforced | `backend/models/api_credential.py:26,43` | UNKNOWN (ротация не реализована) |
| Логи пишут только `user=<id>`, без email/IP/токенов; таблица plaintext email+IP удалена | `backend/routers/auth.py:102-105,164,209` | IMPLEMENTED |
| Sentry-скраббер: без тел запросов, `send_default_pii=False`, no-op при пустом DSN | `backend/services/sentry_setup.py:100-115`, `backend/config.py:68` | IMPLEMENTED (off) |
| Согласие при регистрации на сервер НЕ передаётся (только frontend-checkbox) | `frontend/lib/api.ts:1265-1269`, `backend/schemas/auth.py:7-11`, `frontend/app/register/page.tsx:110-111,318` | GAP (не реализовано) |
| Удаление аккаунта = soft-delete; строка сохраняется, email переиспользуется; папка CSV сносится | `backend/routers/referrals.py:253-274`, `backend/routers/auth.py:151-168` | soft-delete only |
| OAuth отключён: router — fail-closed 403, не смонтирован | `backend/routers/oauth.py:27-30`, `backend/main.py` (нет include) | HOLD/DISABLED |
| Платёжный путь пользователя закрыт; ЮKassa env не задан | `frontend/app/checkout/page.tsx:8-27`, `backend/config.py:71-73`, `backend/routers/payments.py:190` | closed |
| Telegram: отправка пропускается при пустом токене; только opt-in chat_id | `backend/services/telegram.py:11-19`, `backend/config.py:41` | wired, effectively OFF |
| Автопубликация отзывов OFF (3 gate: `automation_enabled`, rule default, consent) | `backend/config.py:107`, `backend/models/automation_rule.py:23-37`, `backend/tasks/auto_publish_reviews.py:106-138` | OFF |
| Все 8 automation/sync/recovery флагов = False; 2 dry-run = True | `backend/config.py:98,107,112,116,131,135,154,157,205,220` | OFF |
| CSV orphan sweep активен: TTL 3600 с, интервал 15 мин, без флага | `backend/routers/csv_import.py:51-55,730-766`, `backend/tasks/uploads_cleanup.py:29,84`, `backend/tasks/scheduler.py:449,704-718` | IMPLEMENTED (active) |
| Observation retention 180/30 дн — feature-flagged OFF | `backend/services/marketplace/retention/observation_sweep.py:43-44`, `backend/config.py:112,116` | OFF |
| Публичный промис «логи ≤ 90 дней» — sweep в коде НЕ найден | claim `frontend/app/privacy/page.tsx:116`; реализация не найдена | UNKNOWN / промис без реализации |
| Полное удаление «в течение 30 дней» — ручной SLA, не джоба | claim `frontend/app/privacy/page.tsx:113` | DOCUMENTED-ONLY |
| Backups/PITR dormant (compose profiles), Object Lock не enabled | `docker-compose.backup.yml:1-14`, `docker-compose.pitr.yml:1-13`, `docs/backup-restore-policy.md:13-15,45-47,107-109` | dormant |
| Base URL по умолчанию localhost; prod hard-fail при localhost | `backend/config.py:37-38,310-313` | dev default |
| Prod hard-fail при незаданных `CRED_ENC_KEY` / `SMTP_HOST` / `SECRET_KEY` | `backend/config.py:301-302,308-309`, credential_vault fail | IMPLEMENTED |
| Живые страницы всё ещё используют `biznes-pult.ru` / `hello@biznes-pult.ru` (домен pult-os.ru не активирован) | `frontend/app/terms/page.tsx:58,70,86`, охрана `backend/tests/test_domain_migration_prep_guard.py:66` | live pages unchanged |
| 2 CSV-файла отслеживаются в Git | `backend/uploads/imports/f28cfd6d-.../*.csv` | BLOCKER (не устранено) |

## B. Нормативные источники (первичные/официальные)

Проверка выполнена офлайн: тексты приводятся по официальным публикаторам ниже, но **сверка по первоисточнику на актуальную редакцию отложена до онлайн-проверки/юриста**. Все правовые выводы — REQUIRES RUSSIAN COUNSEL REVIEW.

| Норма | Что подтверждает для пакета | Официальный источник | Дата сверки |
|---|---|---|---|
| 152-ФЗ «О персональных данных», ст. 5–7, 9 | принципы, условия обработки, требования к согласию | pravo.gov.ru / publication.pravo.gov.ru; Роскомнадзор (rkn.gov.ru) | НЕ СВЕРЕНО ОНЛАЙН |
| 152-ФЗ, ст. 6(3) | содержание поручения обработчику (Selectel/SMTP) | pravo.gov.ru | НЕ СВЕРЕНО ОНЛАЙН |
| 152-ФЗ, ст. 18.1 | политика и локальные акты оператора | pravo.gov.ru | НЕ СВЕРЕНО ОНЛАЙН |
| 152-ФЗ, ст. 18(5) | локализация баз данных граждан РФ | pravo.gov.ru; Роскомнадзор | НЕ СВЕРЕНО ОНЛАЙН |
| 152-ФЗ, ст. 22 | уведомление Роскомнадзора до начала обработки | Роскомнадзор (форма/реестр операторов) | НЕ СВЕРЕНО ОНЛАЙН |
| 149-ФЗ «Об информации…» | статус информационного сервиса, ОРИ и пр. | pravo.gov.ru | НЕ СВЕРЕНО ОНЛАЙН |
| 54-ФЗ «О применении ККТ» | онлайн-касса и чеки при приёме оплаты | ФНС (nalog.gov.ru); pravo.gov.ru | НЕ СВЕРЕНО ОНЛАЙН |
| Закон РФ «О защите прав потребителей» | применимость к B2C-продажам (если физлица) | pravo.gov.ru | НЕ СВЕРЕНО ОНЛАЙН |
| Постановление Правительства РФ № 1119 | требования к защите ПДн / уровни защищённости (УЗ) | pravo.gov.ru | НЕ СВЕРЕНО ОНЛАЙН |
| Приказ ФСТЭК России № 21 (применимая редакция) | состав и содержание мер защиты ПДн в ИСПДн | ФСТЭК России (fstec.ru) | НЕ СВЕРЕНО ОНЛАЙН |
| Документы Selectel (условия обработки/поручение, регионы) | роль обработчика, регион хранения, инциденты | официальные документы Selectel | НЕ СВЕРЕНО ОНЛАЙН |
| Документы будущего SMTP-провайдера | роль обработчика, география, условия | официальные документы провайдера | НЕ ВЫБРАН |

**Спорные трактовки** (например: конкретный УЗ ПДн; необходимость/исключения уведомления Роскомнадзора; квалификация услуги для 54-ФЗ; применимость ЗоЗПП) — **вывод не делается**, помечено **REQUIRES RUSSIAN COUNSEL REVIEW**.
