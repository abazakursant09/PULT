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
| Конкретные auth-события журналируются по user id без plaintext email/IP/токена; таблица plaintext email+IP удалена (НЕ глобальный инвариант) | `backend/routers/auth.py:102-105,164,209` | IMPLEMENTED (scoped, не глобально) |
| SMTP-mailer больше НЕ логирует адрес получателя, subject или текст исключения — только event (`email_sent`/`email_not_sent`/`email_send_failed`) и closed-vocabulary category по ТИПУ исключения; тело/токен по-прежнему не логируются | `backend/services/email.py` (event/category logging + `_smtp_error_category`); guard `backend/tests/test_mail_log_privacy_guard.py` | IMPLEMENTED (LEGAL-PRELAUNCH-D); retention/access логов — отдельный блокер #25 |
| Локализация ПДн граждан РФ — будущее fail-closed обязательство, НЕ доказанный факт (инфраструктура PLANNED/NOT ACTIVE) | ст. 18(5) 152-ФЗ; Selectel `docs/backup-restore-policy.md:19-21`; договор не подписан | DOCUMENTED-ONLY (future contract, привязан к Selectel region gate) |
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
| tracked CSV в current tree = 0 (удалены LEGAL-PRELAUNCH-C1, содержимое не читалось); recursive ignore + guard | `.gitignore` (`backend/uploads/imports/**/*.csv`) + `backend/tests/test_upload_artifact_git_guard.py` | current-tree cleanup IMPLEMENTED |
| Git history purge исторических CSV blobs | старые commits сохраняют blobs; history НЕ переписывалась | **history purge = NOT PERFORMED / REQUIRES SEPARATE DECISION** (blocker #24) |
| Cookie banner: фиктивные `bp_session`/`bp_analytics` удалены; баннер не ставит JS-cookie, не заявляет работающую аналитику (LEGAL-PRELAUNCH-C2) | `frontend/components/CookieBanner.tsx`; охрана `backend/tests/test_cookie_truth_guard.py` | FIXED (юр. квалификация = REQUIRES COUNSEL) |
| Реальная сессия — HttpOnly cookie `__Host-pult_session`/`pult_session_dev`, backend-managed, JS не читает; frontend не хранит/не шлёт токен | `backend/auth_cookie.py:30-31,55-78`; `frontend/tests/sessionCookieGuard.test.ts` | IMPLEMENTED (не изменено C2) |

## B. Нормативные источники (первичные/официальные)

**Метод и границы проверки (честно).** Дата проверки: **2026-08-21**. Проверены только официальные первичные публикаторы: официальный интернет-портал правовой информации `pravo.gov.ru` / `publication.pravo.gov.ru`, Роскомнадзор `rkn.gov.ru`, ФСТЭК России `fstec.ru`, ФНС России `nalog.gov.ru`, `government.ru`. Блоги, агрегаторы и коммерческие пересказы как доказательство НЕ использовались.

Что именно сделано: подтверждены официальные URL первоисточников (официальные домены). Чего НЕ сделано: **полная построчная сверка актуальной редакции каждого акта не выполнена** — прямая загрузка консолидированного текста порталов не завершилась (сетевые/сертификатные ошибки шлюза). Поэтому конкретные редакции/номера пунктов помечены как «URL подтверждён; редакция не сверена построчно», а все правовые выводы остаются **REQUIRES RUSSIAN COUNSEL REVIEW**. Юридическая проверка, которой не было, здесь не изображается. См. блокер #23 в `launch-legal-checklist.md` (official-source line-by-line verification pending).

| Норма | Что подтверждает | Официальный URL | Статус проверки (2026-08-21) |
|---|---|---|---|
| 152-ФЗ «О персональных данных» (ст. 5–7, 9; ч.3 ст.6; ст.18.1; ч.5 ст.18; ст.22) | принципы и условия обработки, согласие, содержание поручения обработчику, локальные акты, локализация, уведомление РКН до начала обработки | http://pravo.gov.ru/proxy/ips/?docbody=&nd=102108261 ; Роскомнадзор https://rkn.gov.ru/treatments/chasto-zadavaemye-voprosy/zashchita-prav-subektov-personalnykh-dannykh/ | URL подтверждён; редакция не сверена построчно |
| Постановление Правительства РФ № 1119 от 01.11.2012 | требования к защите ПДн и 4 уровня защищённости (УЗ); издано во исполнение ст.19 152-ФЗ | http://government.ru/docs/6339/ ; http://pravo.gov.ru/proxy/ips/?docbody=&nd=102160483 | URL подтверждён; редакция не сверена построчно |
| Приказ ФСТЭК России № 21 от 18.02.2013 (применимая редакция — с изм. № 49 от 23.03.2017 и № 68 от 14.05.2020) | состав и содержание орг./тех. мер защиты ПДн в ИСПДн по каждому УЗ | https://fstec.ru/dokumenty/vse-dokumenty/prikazy/prikaz-fstek-rossii-ot-18-fevralya-2013-g-n-21 | URL подтверждён (домен-регулятор); прямой fetch дал cert-ошибку; редакция не сверена построчно |
| 54-ФЗ «О применении ККТ» от 22.05.2003 | онлайн-касса и чеки при приёме оплаты | http://pravo.gov.ru/proxy/ips/?docbody=&nd=102081652 ; ФНС https://www.nalog.gov.ru/rn77/about_fts/docs/3909988/ | URL подтверждён; редакция не сверена построчно |
| 149-ФЗ «Об информации…» | статус информационного сервиса | официальный портал `pravo.gov.ru` (точный документ не сверён 2026-08-21) | UNVERIFIED — official-source verification pending |
| Закон РФ «О защите прав потребителей» | применимость к B2C только если покупатели — физлица | — | **вопрос для юриста, вывод не делается** (REQUIRES RUSSIAN COUNSEL REVIEW) |
| Документы Selectel (поручение/регионы) | роль обработчика, регион, инциденты | — | **CONTRACT PENDING** (договор не подписан) |
| Документы будущего SMTP-провайдера | роль обработчика, география | — | **PROVIDER PENDING** (не выбран) |

**Спорные трактовки** (конкретный УЗ ПДн; необходимость/исключения уведомления Роскомнадзора по ст.22; квалификация услуги для 54-ФЗ; применимость ЗоЗПП) — **вывод не делается**, помечено **REQUIRES RUSSIAN COUNSEL REVIEW**. URL приведены как указатели на первоисточник, а не как подтверждение конкретной трактовки.
