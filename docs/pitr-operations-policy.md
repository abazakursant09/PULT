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

## §15 Canary tooling (SECURITY-2D-3E1B-3C2A) — validates candidates only

3C2A adds DORMANT tooling under `ops/canary/`. It does NOT touch Selectel, create any account/bucket/
user/key, use real credentials, or activate anything. It validates candidate IAM policies OFFLINE and
proves their allow/deny on a TEMPORARY MinIO with synthetic job-local users. **MinIO ≠ Selectel** — a green
MinIO run is a compatibility signal, not a Selectel proof; no Selectel network or resource is exercised.

Roles and closure (candidates, proven minimal only by the future live canary 3C2C):

- The pgBackRest OFFICIAL sample S3 policy includes `s3:ListBucket + s3:GetObject + s3:PutObject +
  s3:DeleteObject`. That sample is a working set, **not** proof of the minimal closure.
- The **active pitr-writer candidate starts WITHOUT `DeleteObject`** (List + Get + Put + multipart only).
  `expire` (deletion of old backups) belongs to the separate **retention-admin** role, not the writer.
- Live **3C2C determines the actual closure**. If it proves the ordinary pgBackRest flow requires Delete,
  the permission is **NOT auto-expanded / not auto-widened** — the exact denied operation is recorded and a
  minimal correction is added only after Inal approval (deny-driven).
- **Object Lock protects locked versions** against deletion, but it does NOT justify broad IAM permissions —
  immutability comes from Object Lock, not from granting/denying Delete.
- logical-writer = Put + Head only (no Get/Delete); restore-reader = List + Get only (no Put/Delete);
  application principal = zero access (explicit Deny of all S3 on the backup bucket).

This section marks **no** external launch-gate item as done. The launch gate above remains **NOT READY**.

## §16 Live canary — 3C2C1 implementation vs 3C2C2 execution

3C2C1 adds a **DORMANT** live mode to `ops/canary/canary.py`. It implements the hard safety gate and the
transport-agnostic orchestration (role matrix, pgBackRest closure probe, Object-Lock probe, exact cleanup),
exercised in CI only against an in-memory FakeTransport or a job-local MinIO. **It does NOT touch Selectel.**

- **Hard gate (fail-closed, before any network/credential read):** requires `--mode live` (positional `live`),
  the explicit env acknowledgement `PULT_SELECTEL_CANARY_LIVE=YES_I_UNDERSTAND`, a typed confirmation equal to
  `project/region/endpoint/bucket/runid`, a `run_id` of 12 hex, `region` in a strict HTTPS endpoint allowlist,
  `endpoint` matching that region's official `https://s3.<region>.storage.selcloud.ru`, and `bucket` equal to
  exactly `pult-canary-<runid>` with prefix `canary/<runid>/`. Any mismatch exits before DNS/credentials.
- **3C2C1 defers real execution:** even when the gate passes, the real `SelectelTransport` is NOT wired — it
  refuses with `SELECTEL_TRANSPORT_NOT_WIRED_UNTIL_3C2C2`. No Selectel account/bucket/user/key/network is
  created or contacted in 3C2C1.
- **Secrets:** credentials come only from environment/file descriptors, never argv; never printed; masked in
  errors; never written to files/artifacts/reports. Ordinary CI never sets the gate env and never names a
  Selectel endpoint (guard-enforced); the offline workflow runs `canary.py live` ONLY as a fail-closed probe
  that treats a live success as a CI failure.
- **Cleanup:** exact resource manifest only (project/bucket/principal/keyID/objectKey/versionId/uploadId);
  no recursive/wildcard/prefix-wide delete; access keys revoked even on failure; bucket removed only after a
  read-back shows no unknown objects; a locked residual is reported as a **controlled residual** (retention
  deadline + max cost), never a false success.
- **pgBackRest closure + Object Lock:** the probe records which S3 operations are actually needed (GetObject
  and DeleteObject necessity recorded **separately**); permissions are **never auto-expanded** — findings feed
  a future 3C2D policy correction. Compliance mode is **never** exercised; only a minimal Governance retention.
- **Runtime freeze:** `canary.py` remains byte-frozen by SHA-256; this reviewed change bumps the pinned digest
  and the `CANARY_RUNTIME_REVIEW` marker together.

**3C2C2** (separate, Inal-approved) wires the real `SelectelTransport` and runs the same orchestration against
temporary real Selectel resources. This section marks **no** external launch-gate item as done. Launch gate
remains **NOT READY**. **MinIO ≠ Selectel.**

## §17 Live S3 transport — 3C2C2-A (implemented, execution still gated)

3C2C2-A wires a REAL Selectel S3 data-plane transport into `ops/canary/canary.py`, but its execution against
a live endpoint remains gated to the separate Inal-approved 3C2C2-B step — the live CLI still defers
(`SELECTEL_EXECUTION_GATED_UNTIL_3C2C2B`). No Selectel account/bucket/key/network in this PR.

- **No new dependency.** SigV4 signing uses the standard library (`hmac`/`hashlib`); HTTPS uses the already
  hash-locked `httpx==0.28.1`, imported lazily only when the real client is built (3C2C2-B). No ad-hoc install.
- **`SelectelS3Transport` hardening:** endpoint must be in `LIVE_REGION_ENDPOINTS` (HTTPS only), TLS verify
  always on, `follow_redirects=False`, `trust_env=False` (no env proxy / credential chain / instance metadata),
  bounded connect/read timeouts, retries for idempotent READS only (mutations never auto-retried), unclassified
  S3 op → treated as mutating and refused, credentials only from validated env/fd (never argv), and the secret
  key / Authorization / signed URL / payload are never logged (private attribute, safe `__repr__`). An
  ambiguous response (no status / unexpected code) → `allow="unknown"` STOP, never a guessed success.
- **Read vs mutation** are explicitly enumerated (`_READ_ONLY_S3_OPS` / `_MUTATING_S3_OPS`).
- **Control plane** (creating projects / service users / keys / policies) is NOT implemented as an automated
  API here — if a safe official API cannot be proven, 3C2C2-B provisions temporary principals via the Selectel
  UI with read-back + immediate rotation; no shell/browser automation, no sole-proprietor document in the repo.
- **SigV4 correctness:** the signing-key HMAC chain matches an independent implementation and the canonical
  request matches the authoritative aws-sig-v4-test-suite `get-vanilla` hash. Byte-correct final signatures are
  proven against a real SigV4 verifier (MinIO / Selectel) only in **3C2C2-B** — an explicit UNKNOWN until then.
- **Runtime freeze** re-pinned; `CANARY_RUNTIME_REVIEW = 3C2C2A-selectel-transport-dormant`.

3C2C2-A safety is proven offline: FakeHTTP client + socket network-trap (0 external connections), no secret
leakage, endpoint allowlist enforced, mutation-no-retry, exact-cleanup unchanged. Launch gate remains **NOT
READY**. **MinIO ≠ Selectel.**

## §18 Live execution gate — 3C2C2-B (wired, offline-proven; Gate F not run)

3C2C2-B wires the real live execution path into `ops/canary/canary.py` behind a strict one-time execute
gate. It is proven only offline (FakeTransport + socket network-trap); **no Selectel account/bucket/key/
network is touched in this PR**, and CI never runs it.

- **Ordinary `live` still defers**: without `--execute-live` it prints `SELECTEL_EXECUTION_GATED_UNTIL_3C2C2B`
  and exits (no network). Execution requires the explicit `--execute-live` gate below.
- **Execute gate (fail-closed BEFORE any DNS/socket/credential read):** `--execute-live` AND the env
  acknowledgement AND a typed confirmation AND `--ack == PULT-CANARY-EXECUTE-<runid>` AND region pinned to
  **ru-3** AND endpoint == official ru-3 S3 AND `--max-object-bytes` in (0, 10 MiB] AND `--deadline` a UTC
  timestamp AND bucket == `pult-canary-<runid>`. Any mismatch → exit 4 before credentials/network.
- **Credentials** are read ONLY after the gate passes, via `read_masked_credentials` (getpass, no echo, memory
  only; never argv / file / shell history / Git / logs). Order is enforced: `execute_validate` before any
  credential read.
- **Orchestration** (`run_live_execution`, injected transport factory + clock) runs the role allow/deny matrix
  against per-role transports, then EXACT data-plane cleanup in a `finally`. Control-plane revocation
  (keys/users/policies) is MANUAL and only RECORDED for Inal (`manual_revoke_required`). Any matrix failure or
  cleanup residual → `CONTROLLED_RESIDUAL`/`FAILED`, never a hidden success.
- **Runtime freeze** re-pinned; marker `CANARY_RUNTIME_REVIEW = 3C2C2B-live-execution-gated`.

Gate-F actual run (real keys on an isolated machine, disposable ru-3 canary) remains a separate Inal step.
Launch gate **NOT READY**. **MinIO ≠ Selectel.**

## §19 Pre-live correction (3C2C2-B) — honest live path

An independent pre-live review found real defects in the first live wiring; this correction fixes them. No
Selectel resource is created; proven offline (attempt()-only FakeTransport + socket network-trap).

- **Cleanup uses only `attempt()`** (the real transport's sole method). `run_cleanup(admin_transport, manifest,
  ledger, clock)` issues exact `AbortMultipartUpload` / `DeleteObjectVersion` (with explicit versionId) / final
  `DeleteBucket`. Bucket deletion is the fail-closed read-back: S3 refuses to delete a non-empty bucket, so a
  non-2xx `DeleteBucket` ⇒ unknown residual ⇒ `CONTROLLED_RESIDUAL`. No phantom `delete_user`/`remove_bucket`
  methods (the earlier code would have raised AttributeError in `finally`). IAM keys/users/policies are
  **control-plane = MANUAL**, only RECORDED in `manual_revoke_required`; the canary never revokes them.
- **Object Lock IS reached live**: `run_live_execution` creates exactly one synthetic object, applies
  `PutObjectRetention` **Governance** with retain-until = deadline (≤15 min), and proves a writer's
  `DeleteObjectVersion` is **DENIED** while locked. **Compliance and BypassGovernanceRetention are never used.**
  Because the locked object cannot be deleted before expiry, the honest live outcome is **CONTROLLED_RESIDUAL**
  until expiry — a follow-up exact cleanup deletes it after retain-until. Never reported as a false clean.
- **pgBackRest live closure is NOT attempted here** (`pgbackrest_closure = "NOT-ATTEMPTED-live"`). It needs a real
  pgBackRest binary run and is a separate later step. The offline `pgbackrest_probe` is a design helper only.
- **Deadline** is enforced with an injected clock: `execute_validate` requires `now < deadline ≤ now + 30 min`
  (past / too-far / malformed → exit 4 pre-network); `run_live_execution` checks `clock() < deadline_dt` before
  every operation and STOPs remaining ops after expiry, but cleanup still runs in `finally`.
- **Addressing = Path-Style** (host = `s3.ru-3.storage.selcloud.ru`, URL = endpoint + `/bucket/key`), matching
  the SigV4 host header and pgBackRest `uri-style=path`. **Gate C must therefore use Path-Style, not vHosted —
  a checklist change requiring Inal's confirmation.**
- **Runtime freeze** re-pinned; marker `3C2C2B-prelive-correction`.

### Windows launcher (Gate F, isolated machine — PowerShell)
Secrets are NEVER in argv/env/file/history — only the non-secret acknowledgement is exported; the 10 key values
are entered at masked `getpass` prompts into process memory:
```
# PowerShell, isolated machine, clean session (history off):
$env:PULT_SELECTEL_CANARY_LIVE = 'YES_I_UNDERSTAND'
python ops\canary\canary.py live --execute-live `
  --project-id <PROJECT_ID> --region ru-3 `
  --endpoint https://s3.ru-3.storage.selcloud.ru `
  --bucket pult-canary-<runid> --run-id <runid> `
  --confirm "<PROJECT_ID>/ru-3/https://s3.ru-3.storage.selcloud.ru/pult-canary-<runid>/<runid>" `
  --ack PULT-CANARY-EXECUTE-<runid> --max-object-bytes 1048576 --deadline <UTC now+15min>
Remove-Item Env:\PULT_SELECTEL_CANARY_LIVE   # clear the ack env afterwards
```
Launch gate remains **NOT READY**. **MinIO ≠ Selectel.**

## §20 Pre-live correction-2 — real Object-Lock proof + cleanup contract

A second review found the Object-Lock "proof" was false-positive. Fixed:

- **Real Object-Lock proof** (`run_live_execution`): **pitr-writer creates both control objects** (its
  policy grants PutObject on `canary/<runid>/pitr/*`; retention-admin has NO PutObject — least privilege). (1)
  pitr-writer PUTs an UNLOCKED control; retention-admin **can** DeleteObjectVersion it → IAM allows delete; (2)
  pitr-writer PUTs a locked control; retention-admin `PutObjectRetention` with a **valid signed XML body** (`Mode=GOVERNANCE`, `RetainUntilDate`,
  content-md5 + content-type SIGNED) → expect 2xx; (3) `GetObjectRetention` read-back, parse XML fail-closed and
  assert `Mode==GOVERNANCE` + `RetainUntilDate`==sent + versionId==same; (4) THEN the same retention-admin
  attempts DeleteObjectVersion → expect **AccessDenied**. Only `iam_delete_ok ∧ retention_set ∧ readback_ok ∧
  locked_delete_refused` counts as proof. The pitr-writer's DeleteObjectVersion deny stays in the IAM role
  matrix — it is **not** used as Object-Lock proof (it is IAM-denied regardless). No Compliance, no Bypass.
- **Cleanup contract = Variant 1 (manual post-expiry)**: `run_cleanup` deletes ONLY the exact unlocked object
  versions and multipart uploads it created, via `attempt()` only. It does **NOT** call DeleteBucket (removed —
  retention-admin has no `s3:DeleteBucket`) and never attempts to delete the locked object before expiry. The run
  ends **CONTROLLED_RESIDUAL** with a secret-free ledger (bucket/key/versionId/retainUntil). Bucket + keys/users/
  policies + project + the post-expiry locked-object delete are **MANUAL (Gate F6)**, only recorded.
- **Exact Gate-F live bucket policy** is versioned at `ops/canary/gate-f-live-bucket-policy.json` (placeholders
  `<BUCKET>`/`<RUNID>`/`<UID-*>`), marker `NOT_FOR_ROUTINE_BACKUP`, retention-admin scoped to `canary/<RUNID>/*`
  with `PutObjectRetention`+`DeleteObject`+`DeleteObjectVersion` but **no BypassGovernanceRetention, no
  DeleteBucket, no Compliance, no lifecycle**; app = Deny s3:*. Guard-checked.
- **Path-Style stays PROPOSED** — Gate C change to Path-Style still needs Inal's explicit approval.
- **Runtime freeze** re-pinned; marker `3C2C2B-prelive-correction`.

## SECURITY-2D-3E1B-3C2C2-B-DIAG — read-only post-run diagnostic (DORMANT)

The single Gate-F live run ended `status=FAILED` / exit 6 and its in-memory detail (role matrix, Object-Lock
booleans, cleanup ledger) is gone — nothing was ever written to a file or log, so the exact FAILED cause is
**not recoverable**. `ops/canary/diagnose.py` is a separate, dormant, **read-only** inspector of the CURRENT
bucket state; it never re-runs the canary, never widens IAM/policy, and touches `canary.py` not at all.

- **Exactly five read-only S3 ops**: `GetBucketVersioning`, `GetBucketObjectLockConfiguration`, `ListBucket`
  (exact prefix `canary/<runid>/` only), `HeadObject` (the four exact synthetic keys only), and
  `GetObjectRetention` (the lock key only). No write, delete, retention-mutation, Bypass, or Compliance op is
  reachable — an AST guard proves it and the transport is driven single-shot for reads only.
- **Hard-pinned scope**: bucket/run-id/prefix/region/endpoint are constants; no argv or env can widen it.
- **Credentials** come only from masked getpass — retention-admin required (it holds ListBucket/GetObject/
  GetObjectRetention + bucket-config reads), restore-reader optional for an independent existence cross-check.
  The bucket owner intentionally has no object access, so its UI "Access denied" proves nothing; policy is
  **not** expanded for the diagnostic. Credentials never reach argv/env/file/log; `PROJECT_ID`/UID are neither
  needed nor accepted.
- **Gate**: separate env acknowledgement `PULT_SELECTEL_CANARY_DIAGNOSE`, a typed confirm, a `--ack`, and a
  deadline in `(now, now+30min]` — all validated **before** any getpass/transport/DNS/socket; ordinary
  invocation fails closed pre-network; execution needs `--execute-diagnose`.
- **Output** is a fixed secret-free allowlist: the state fields (versioning, object_lock, the four `*_exists`,
  lock retention mode + retain-until UTC), a per-read `*_read_status` for each of the eight reads, a
  `diagnostic_error_summary`, and `diagnostic_status` PASS/PARTIAL/FAILED. It never prints a secret, version
  id, request id, UID/PROJECT_ID, HTTP body, URI, raw transport result, or a request-bearing stack trace.
- **Safe error classification (3C2C2-B-DIAG-CORRECTION)**: the first read-only run returned every field
  `unknown` because the tool over-redacted all failures. Each read is now mapped to ONE secret-free category —
  `ok, not-found, invalid-access-key, signature-mismatch, access-denied, authentication-failed, timeout,
  tls-error, network-error, service-error, malformed-response, unknown` — derived ONLY from the transport
  `allow`, the numeric HTTP status class, an allowlisted XML `<Code>` (InvalidAccessKeyId / SignatureDoesNotMatch
  / AuthorizationHeaderMalformed / AccessDenied / InvalidToken / ExpiredToken; anything else falls back to the
  status class or `unknown`), or a fixed local exception-type category. The body is read ONLY for the `<Code>`
  tag — never Message/Resource/RequestId/HostId/StringToSign/CanonicalRequest; malformed XML → `malformed-response`.
  `diagnostic_error_summary` is `none` (no errors), the single category (all errors equal), or `mixed`. An
  access-denied / signature-mismatch / invalid-key read keeps the state field `unknown` and yields PARTIAL/FAILED —
  a failure is never turned into success.
- **Honest limits**: results describe only current state. They do not reconstruct the past role matrix; an
  empty bucket does not explain the failure; a lock object without retention indicates an incomplete
  Object-Lock path; residual probe/unlocked objects indicate a cleanup residual. No stronger claim is made.

Launch gate remains **NOT READY**. **MinIO ≠ Selectel.**
