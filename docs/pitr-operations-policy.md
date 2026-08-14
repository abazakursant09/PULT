# PITR / backup production operations policy (SECURITY-2D-3E1B-3C1 — DORMANT foundation)

## Кратко (простым русским)

- B1 и B2 доказали **механизм** резервного копирования/PITR только на **синтетических** данных против
  MinIO. Это НЕ production-защита.
- **Production backup пока НЕ работает**: нет сервера, нет bucket, нет credentials, нет расписания,
  нет мониторинга. Ни одной реальной offsite-копии production-данных не существует.
- **3C1 ничего не активирует** — это только зафиксированные контракты/правила/документация + offline
  guard. Поведение ПУЛЬТа не меняется.
- **Запуск (launch/deploy) запрещён** до полного прохождения бинарного launch gate (см. §14).
- **MinIO ≠ Selectel** — поведение на MinIO не доказывает поведение Selectel.
- **RPO=0 during an S3 outage is NOT promised** — при недоступности S3 и потере VDS ещё не выгруженный
  WAL может быть потерян.
- **production PITR is NOT activated** — production `docker-compose.yml` не содержит archive_mode/
  archive_command/pgBackRest; профиль `pitr` дормантный.

## §2 Статус этапов и границы

| Этап | Что делает | Внешние эффекты | Требуется |
|---|---|---|---|
| **3C1** (этот PR) | dormant contracts + docs + offline guard | НЕТ | Inal-review |
| **3C2** | реальный Selectel bucket / IAM / Object-Lock **canary** tooling | Selectel-ресурсы (Inal-gated) | отдельный PR + Inal |
| **3C3** | dormant host scheduling (systemd timers) + monitoring/dead-man | поставка юнитов, не включение | отдельный PR + Inal |
| **3C4** | controlled production activation procedure + launch gate исполнение | активация PITR | отдельный PR + Inal |
| **3C5** | первый реальный restore drill + измерение RPO/RTO | restore на новой инфраструктуре | отдельный PR + Inal |

Каждый следующий этап — **отдельный PR и отдельное одобрение Inal**. 3C2, 3C3, 3C4, 3C5 разделены и НЕ
входят в 3C1.

**Зафиксированное внешнее состояние** (не доказуемо из репозитория, зафиксировано как факт проекта,
источник: memory beta-server-plan + отсутствие артефактов в репозитории):
- сервер (VDS) ещё НЕ куплен;
- production data отсутствуют;
- backup bucket отсутствует;
- credentials отсутствуют;
- production PITR НЕ активирован;
- scheduler / dead-man / monitoring отсутствуют.

## §3 Bucket architecture (рекомендуемая, ещё НЕ создана)

- Отдельный production backup bucket. Пример-плейсхолдер: `<pult-prod-backup-bucket>` (реальное имя НЕ
  фиксируется здесь как существующее).
- Только **российский регион** (152-ФЗ локализация). По возможности регион, **отличный от региона будущего
  VDS** (cross-region offsite, чтобы падение одного ДЦ не унесло prod и backup одновременно).
- Bucket НЕ используется для app uploads. dev / staging / prod НЕ смешиваются (отдельные bucket).
- Имя bucket без PII, email, tenant или marketplace identifiers.
- Prefixes: `/pitr/` (pgBackRest repo), `/logical/` (3A age-бэкапы), `/status/` (манифесты/статус).
- pgBackRest stanza: `pult`.
- **versioning обязательно**; **Object Lock включается при создании bucket** (позже включить нельзя).
- Retention administrator НЕ находится на application VDS.

## §4 IAM contract — три роли (deny имеет приоритет; реальные permissions подтверждает только 3C2 canary)

### Role A — Backup/PITR writer (на VDS: pgBackRest + 3A)
Желаемый минимум: `PutObject`; multipart-операции, нужные pgBackRest/rclone (`AbortMultipartUpload`,
`ListMultipartUploadParts`); prefix-scoped `ListBucket` / `HeadObject`; только собственные prefixes.
БЕЗ `DeleteObject`; БЕЗ lifecycle / Object-Lock / bucket-policy administration.

**Критическая честность (GET closure PROVISIONAL):** НЕ утверждается до 3C2, что writer гарантированно
работает без `GetObject` — pgBackRest может читать repository metadata и existing objects. Поэтому:
точный минимальный набор GET/LIST/HEAD/PUT определяется **runtime canary**; любое разрешение добавляется
**только после доказанного AccessDenied**; canary фиксирует фактически использованные операции; writer
всё равно НЕ получает Delete/admin без отдельного решения.

### Role B — Restore reader (только во время restore)
prefix-scoped `ListBucket`; `GetObject` / `HeadObject`. БЕЗ `PutObject` / `DeleteObject` / admin.
Credentials выдаются только на время restore; age private identity — только у restore-контура, не на VDS
постоянно.

### Role C — Retention / Object-Lock administrator
Отдельный principal, НЕ на application VDS. Управляет retention / lifecycle / Object Lock. pgBackRest
`expire` / `DeleteObject` выполняется **только этой ролью либо отдельной временной expire-role** (writer
expire НЕ выполняет). `BypassGovernanceRetention` — только по утверждённой процедуре. Не используется
обычным backup/restore процессом.

Во всех ролях: **app credentials НЕ имеют доступа к backup bucket**; wrong-prefix и cross-environment
запрещены; deny-precedence.

## §5 Selectel facts vs unknowns

**Подтверждено официальной документацией (на 2026-08):**
- Российские регионы и endpoint-форматы: `s3.ru-1.storage.selcloud.ru` (SPb), `s3.ru-7.storage.selcloud.ru`
  (Moscow), ru-3 (SPb), ru-6 (Moscow Multi-AZ) — источник: [Selectel S3 API/regions](https://docs.selectel.ru/en/api/object-storage-s3/).
- Bucket policy с гранулярными actions (Put/Get/Delete/AbortMultipartUpload/ListMultipartUploadParts/
  ListBucket/versioning + Object-Lock actions incl. `BypassGovernanceRetention`), prefix + principal +
  Allow/Deny(deny-wins) — [bucket policy](https://docs.selectel.ru/en/s3/buckets/bucket-policy/about-bucket-policy/),
  [access management](https://docs.selectel.ru/en/s3/manage/manage-access/).
- Versioning и Object Lock при создании bucket; Object Lock требует versioning (авто-включается); multipart
  поддержан, incomplete multipart авто-очередь на удаление через 6 месяцев — [create bucket](https://docs.selectel.ru/en/s3/buckets/create-bucket/).
- Governance-related permission (`BypassGovernanceRetention`) присутствует.

**Нужно подтвердить в 3C2 canary (НЕ переносить AWS-семантику на Selectel без доказательства):**
Signature V4; path-style vs virtual-host; точный endpoint выбранного региона; точный pgBackRest IAM
closure; Compliance mode; explicit lifecycle expiration rules; server-side encryption; access/audit logs;
quotas/rate limits; notifications; стоимость; eventual-consistency/visibility Selectel.

## §6 Object Lock и retention (правила; окончательные сроки — решение Inal)

- **Governance** — рекомендуемый стартовый режим, требует решения Inal.
- **Compliance mode: do NOT enable** без отдельного юридического и операционного решения (может навечно
  заблокировать законное уничтожение ПДн).
- versioning обязательно.
- lifecycle НЕ должен удалять WAL, необходимый живому full backup.
- pgBackRest retention и bucket lifecycle имеют **одного владельца** (Role C).
- writer НЕ выполняет expire.
- retention admin выполняет **dry-run до enforce**.
- legal hold только вручную.
- Object Lock НЕ должен бессрочно препятствовать законному уничтожению ПДн.
- сроки хранения утверждаются Inal после консультации с юристом.
- WAL / full / logical retention математически согласованы.

**Retention formula (инвариант, не финальная конфигурация):**

```
WAL retention >= oldest retained full backup age
               + max interval between full backups
               + safety margin
```

## §7 Encryption и secret inventory

Слои: (1) **TLS** in-transit; (2) **pgBackRest repository cipher** (aes-256-cbc); (3) **age encryption**
(3A логические бэкапы); (4) **Selectel SSE** — если подтвердится в 3C2.

**Secret categories** (список фиксирует КАТЕГОРИИ, не число):
- writer access key; writer secret key;
- restore reader access key; restore reader secret key;
- retention admin credentials;
- pgBackRest repository cipher passphrase;
- age public recipient; age private identity;
- PostgreSQL backup credential (`pult_backup`);
- monitoring / dead-man credential.

Для каждого секрета: владелец; место использования; где запрещено хранить; rotation; отзыв (revocation);
поведение при потере; emergency access.

**Секреты ЗАПРЕЩЕНО хранить в:** Git; Docker image; Compose YAML; CI logs/artifacts; MEMORY.md; shell history; application environment; Sentry.

**Механизм доставки пока НЕ выбран окончательно** (зависит от будущего VDS, относится к 3C3/3C4).
Кандидаты: root-owned host file mode `0600`; Docker secrets; systemd credentials; managed secret store.

## §8 Disk / backlog capacity

**Capacity formula:**

```
reserve_bytes = measured_peak_wal_bytes_per_second
              × maximum_tolerated_s3_outage_seconds
              × safety_factor
```

- synthetic backlog **128 MB НЕ использовать как production capacity**.
- synthetic **drain rate НЕ использовать как production SLO**.
- WAL-rate измеряется на реальной нагрузке ДО активации.
- safety factor утверждается Inal.
- запрещено автоматически удалять невыгруженные WAL.
- запрещено `docker compose down -v` (уничтожает `postgres_data`).
- production activation запрещена без измеренного disk reserve.
- warning / critical / emergency thresholds задаются только после измерения.
- **VDS loss during local backlog can lose not-yet-offsite WAL** (остаточный риск).

## §9 Activation state machine (fail-closed; ничего не выполняется в 3C1)

Общее правило: **No single feature flag or single successful command can activate the whole contour.**
Каждый переход требует approval Inal.

### Phase 0 — prerequisites
Prereq: сервер куплен+hardened; bucket создан; IAM canary GREEN; Object Lock/versioning проверены;
secrets доставлены; disk reserve измерен; monitoring channel; свежий backup; maintenance window;
rollback owner. Operator: ops. Approval: Inal. Evidence: canary/backup logs. Timeout: N/A. STOP: любой
prereq не выполнен. Rollback: не начинать.

### Phase 1 — dormant deployment
Действие: PITR image/config доставлены; production archive ещё НЕ считается активным; **no automatic
restart; no flags.** Operator: ops. Approval: Inal. Evidence: image digest present. Timeout: N/A.
STOP: digest/parity mismatch. Rollback: удалить артефакты.

### Phase 2 — controlled PostgreSQL restart
Действие: pre-change evidence; logical+physical backup; controlled write handling (окно); restart;
readiness; schema/Alembic head `rob1a2b3c4d01`; application health. Operator: ops. Approval: Inal.
Evidence: pre/post snapshots, health. Timeout: bounded. STOP: data loss / health fail. Rollback: вернуть
prev image + восстановить.

### Phase 3 — first exact WAL gate
Действие: controlled transaction → `pg_switch_wal` → exact segment local → exact segment offsite →
`pgbackrest check` → `status.sh continuity=intact` → alert path operational. Operator: ops. Approval: Inal.
Evidence: exact-segment offsite + continuity. Timeout: bounded. STOP: не offsite / continuity≠intact.
Rollback: отключить archive, вернуться в Phase 1.

### Phase 4 — operations enablement
Действие: full backup scheduling; logical backup scheduling; retention dry-run→enforce; monitoring/
dead-man; restore drill schedule. Operator: ops. Approval: Inal. Evidence: alert delivered, retention
dry-run. Timeout: N/A. STOP: dead-man не доставлен. Rollback: отключить таймеры.

## §10 Scheduling / monitoring — только требования (код НЕ добавляется в 3C1)

Будущее (3C3): host-level orchestration, **НЕ application scheduler**; рекомендуемый кандидат — systemd
timer + hardened one-shot service; locking (без overlap); bounded timeout; UTC; missed-run handling;
reboot behavior; safe (allowlist) logs; **dead-man**; repository check; last exact offsite WAL; backlog;
last full backup; restore-drill age. В 3C1 НЕ добавляются systemd units, cron, webhook или monitoring-код.

## §11 Restore drill contract

Только новая изолированная инфраструктура; **никогда поверх production PGDATA** (`restore.sh` уже отказывает
при существующем `PG_VERSION`). Шаги: incident declaration; freeze writes; выбрать full + target LSN/time;
временно получить reader credentials; restore в **new empty PGDATA**; integrity; Alembic head; application
HTTP smoke; marketplace/provider writes выключены; reconciliation; controlled cutover; старый production
остаётся read-only; rollback; evidence без секретов. **RTO is NOT proven until the first real drill.**

## §12 RPO/RTO decision table

| Параметр | Экономный | Рекомендуемый | Усиленный |
|---|---|---|---|
| WAL archive | continuous | continuous | continuous |
| full backup | weekly | 2×/week | daily |
| logical backup | weekly | weekly | daily |
| retention | 7–14 дн | 30 дн | 30–90 дн |
| drill frequency | quarterly | monthly | monthly + cross-region |
| cross-region | нет | опц. | да |
| disk reserve | 1× outage | 2× | 3× |
| target RPO | до последнего offsite при outage | меньше окно | ≈мин |
| target RTO | часы | часы | час |
| residual | спул-WAL на VDS потерян при VDS-loss | тот же, меньше окно | минимизирован репликой |

Маркировка каждого значения: **proposed target** / **synthetically proven** / **production measured** /
**contractually promised**. Ни один target НЕ называется доказанным. RPO/RTO target ≠ measured.

**Решения, которые должен принять Inal:** будущий регион VDS; backup region; бюджет; retention period;
Governance/Compliance; RPO target; RTO target; drill frequency; cross-region replication; список операторов
доступа.

## §13 Юридический pre-launch checklist (это НЕ юридическое заключение / NOT a legal opinion)

Вопросы до launch: backup содержит персональные данные; хранение в РФ-регионе; статус оператора/обработчика;
договор с Selectel; поручение обработки; организационные и технические меры; список сотрудников с доступом;
журнал доступа; сроки хранения; уничтожение после срока; incident response; отсутствие трансграничной
передачи либо отдельное оформление; Object Lock и право на уничтожение; консультация профильного юриста.
Соответствие 152-ФЗ/149-ФЗ НЕ утверждается на основании этого документа.

## §14 Binary launch gate — DEFAULT: NOT READY

Launch/deploy ЗАПРЕЩЁН, пока НЕ доказано ВСЁ (3C1 НЕ помечает ни один внешний пункт выполненным):

- [ ] утверждённый российский регион
- [ ] bucket создан
- [ ] Object Lock / versioning проверены
- [ ] IAM canary GREEN
- [ ] роли writer / reader / admin разделены
- [ ] secret delivery проверена
- [ ] первый full backup offsite
- [ ] первый exact WAL offsite
- [ ] continuity=intact
- [ ] alert / dead-man доставлен
- [ ] retention dry-run и enforce проверены
- [ ] disk reserve измерен
- [ ] restore drill на новой инфраструктуре успешен
- [ ] application HTTP smoke после restore
- [ ] RPO/RTO измерены и утверждены
- [ ] runbook подписан
- [ ] юридический checklist закрыт либо риски письменно приняты
- [ ] нет красных security residuals без решения

Текущий статус: **NOT READY.**
