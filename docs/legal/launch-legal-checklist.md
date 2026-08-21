# Launch legal checklist / gap report — Пульт OS

Статус: **DRAFT / INTERNAL**. База: `origin/master` `5584cd6`. Столбцы обязательности отвечают на вопрос: «нужно ли ДО этого рубежа?».

Статусы: **DONE** / **PARTIAL** / **BLOCKED** (нельзя двигаться, пока не решено внешне) / **UNKNOWN** (нет доказательства).

Владельцы: **Inal** / **developer** / **lawyer** / **Selectel** / **mail** (почтовый/SMTP-провайдер).

| # | Требование | Текущее доказательство | Статус | Владелец | До staging | До production | До 1-й оплаты |
|---|---|---|---|---|---|---|---|
| 1 | **Mail Gate**: рабочий ящик на pult-os.ru для обращений субъектов ПДн | почта не активна; контакты помечены NOT ACTIVE | BLOCKED | Inal + mail | нет | **да** | да |
| 2 | Финальные реквизиты оператора (ИНН/ОГРНИП/адрес) | плейсхолдеры `[REQUIRES INAL — INSERT LOCALLY]` | BLOCKED | Inal | нет | **да** | да |
| 3 | Договор/поручение Selectel (ст. 6(3) 152-ФЗ) + локализация ПДн граждан РФ (ст. 18(5)) до начала production: данные, цель, операции, регионы, инциденты, уничтожение | Selectel = PLANNED/CONTRACT PENDING; сервер не заказан; локализация — будущее обязательство, не факт | BLOCKED | Inal + Selectel + lawyer | нет | **да** | да |
| 4 | Определение УЗ ПДн и модель угроз (ПП №1119, Приказ ФСТЭК №21) | конкретный УЗ не заявлен | BLOCKED | lawyer + Inal | нет | **да** | да |
| 5 | Решение по уведомлению Роскомнадзора (ст. 22 152-ФЗ) | не подано; вывод не сделан | BLOCKED | lawyer + Inal | нет | **да** | да |
| 6 | Server-side consent evidence (версия+UTC-время+способ) | согласие только на frontend, на сервер не идёт | BLOCKED | developer | рекомендуется | **да** | да |
| 7 | Hard-delete / анонимизация вместо soft-delete | `DELETE /account` = деактивация; строка сохраняется | BLOCKED | developer + lawyer | нет | **да** | да |
| 8 | Реализация и проверка сроков хранения по каждой категории | enforced только 1-ч TTL CSV; остальное OFF/UNKNOWN | PARTIAL | developer + lawyer | нет | **да** | да |
| 9 | Процедура запросов субъектов (доступ/исправление/блокирование/выгрузка/удаление) | нет self-service; экспорт не найден | BLOCKED | developer + lawyer | нет | **да** | да |
| 10 | Incident response (в т.ч. уведомление РКН об инцидентах) | процедуры нет в документах | BLOCKED | lawyer + Inal | нет | **да** | да |
| 11 | SMTP agreement + география данных | provider не выбран; в dev письмо логируется | BLOCKED | Inal + mail | рекомендуется | **да** | да |
| 12 | Устранить публичный промис «логи ≤ 90 дней» или реализовать TTL | claim `privacy:116`; sweep не найден | BLOCKED | developer | рекомендуется | **да** | да |
| 13 | Убрать фиктивные cookie `bp_session`/`bp_analytics` из баннера или подкрепить реализацией | `CookieBanner.tsx:19,21,51-55` | BLOCKED | developer | рекомендуется | **да** | да |
| 14 | Удалить 2 отслеживаемых CSV из Git (уже загруженные данные не хранить в репо) | `backend/uploads/imports/.../*.csv` | BLOCKED | developer | **да** | да | да |
| 15 | Тарифы / возвраты / онлайн-касса / чеки по 54-ФЗ | не определены; оферта — каркас | BLOCKED | Inal + lawyer | нет | нет | **да** |
| 16 | ЮKassa: договор + активация + вебхук | код есть, путь закрыт, env не задан | BLOCKED | Inal | нет | нет | **да** |
| 17 | Финальная оферта (после 15–16) | HOLD-каркас | BLOCKED | lawyer + Inal | нет | нет | **да** |
| 18 | Решение по OAuth Google/Apple/Яндекс + иностранные AI/API + трансграничная передача | HOLD; router DISABLED | BLOCKED (HOLD) | lawyer + Inal | нет | по решению | по решению |
| 19 | Ротация/отзыв marketplace-credentials (`expires_at` не enforced) | `api_credential.py:26,43` | UNKNOWN | developer | нет | рекомендуется | рекомендуется |
| 20 | Активация домена pult-os.ru (DNS + сайт) и переключение legal-страниц | live-страницы всё ещё `biznes-pult.ru` | BLOCKED | Inal + developer | нет | **да** | да |
| 21 | Финальная проверка профильным юристом РФ (152-ФЗ/149-ФЗ/54-ФЗ/ЗоЗПП) | не проведена | BLOCKED | lawyer | рекомендуется | **да** | да |
| 22 | SMTP / application-log: убрать или псевдонимизировать email получателя и `str(exc)` в логах mailer; определить retention/access для application logs | `services/email.py:49,53,56` логирует `to=` + subject + exc | BLOCKED | developer | рекомендуется | **да** | да |
| 23 | Official-source line-by-line verification нормативных актов (полная сверка редакций 152-ФЗ/1119/ФСТЭК №21/54-ФЗ по первоисточнику) | URL подтверждены 2026-08-21; построчная сверка не завершена | UNKNOWN | lawyer + developer | рекомендуется | **да** | да |

## Минимальные блокеры до первой оплаты

Mail Gate (#1) → реквизиты (#2) → поручение Selectel + локализация (#3) → УЗ+модель угроз (#4) → решение по РКН (#5) → server-side consent (#6) → hard-delete/anonymization (#7) → retention (#8) → процедура запросов субъектов (#9) → incident response (#10) → SMTP agreement + география (#11) → устранить промис «логи ≤90 дней» или реализовать TTL (#12) → убрать фиктивные `bp_session`/`bp_analytics` (#13) → удалить два отслеживаемых CSV из Git (#14) → тарифы/возвраты/касса/чеки (#15) → ЮKassa (#16) → финальная оферта (#17) → SMTP/application-log: убрать email+exc из логов (#22) → official-source verification (#23) → review юриста РФ (#21).

## Резюме

- **launch gate = NOT READY.** Ни один документ пакета не готов к публикации.
- production, платежи, DNS/домен, PITR/scheduler — **OFF**.
- Пакет — документация, не активирует ничего и не является юридическим заключением.
