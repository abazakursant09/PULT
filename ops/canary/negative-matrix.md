# Canary operation matrix (contract) — SECURITY-2D-3E1B-3C2A

**MinIO ≠ Selectel.** Rows tiered `offline` / `minio` are executed now (offline JSON validation and
temporary-MinIO allow/deny). Rows tiered `future-selectel` are `NOT EXECUTED IN 3C2A` — they are the
contract for the live canary (3C2C) and are proven only with real Selectel credentials + resources after
Inal's decisions. A green MinIO row is a compatibility signal, **not** a Selectel proof.

Rules: **unexpected allow is always FAIL. Unexpected deny of a required operation is always FAIL and the
policy is NOT auto-widened** — the exact denied operation is recorded and a minimal permission is added only
after Inal approval (deny-driven, never automatic). Cleanup deletes only the exact synthetic key / version-id /
multipart-upload-id created by the current random run-id — never recursive/wildcard/bucket-prune.

## Positive + negative (offline + MinIO)

| role | operation | resource prefix | expected | accepted error | timeout | retry | evidence | cleanup | tier |
|---|---|---|---|---|---|---|---|---|---|
| logical-writer | PutObject | `logical/` | allow | — | 60s | reads only | mc rc=0 | rm exact key | minio |
| logical-writer | stat/Head | `logical/` | allow | — | 60s | reads only | mc rc=0 | none | minio |
| logical-writer | GetObject | `logical/` | deny | AccessDenied | 60s | none | mc AccessDenied | none | minio |
| logical-writer | DeleteObject | `logical/` | deny | AccessDenied | 60s | none | mc AccessDenied | none | minio |
| logical-writer | PutObject | `pitr/` (wrong) | deny | AccessDenied | 60s | none | mc AccessDenied | none | minio |
| pitr-writer | PutObject | `pitr/` | allow | — | 60s | reads only | mc rc=0 | rm exact key | minio |
| pitr-writer | GetObject | `pitr/` | allow | — | 60s | reads only | mc rc=0 | none | minio |
| pitr-writer | ListBucket(prefix) | `pitr/` | allow | — | 60s | reads only | mc rc=0 | none | minio |
| pitr-writer | DeleteObject | `pitr/` | deny | AccessDenied | 60s | none | mc AccessDenied | none | minio |
| pitr-writer | PutObject | `logical/` (wrong) | deny | AccessDenied | 60s | none | mc AccessDenied | none | minio |
| restore-reader | ListBucket(prefix) | `pitr/` | allow | — | 60s | reads only | mc rc=0 | none | minio |
| restore-reader | GetObject | `pitr/` | allow | — | 60s | reads only | mc rc=0 checksum | none | minio |
| restore-reader | PutObject | `pitr/` | deny | AccessDenied | 60s | none | mc AccessDenied | none | minio |
| restore-reader | DeleteObject | `pitr/` | deny | AccessDenied | 60s | none | mc AccessDenied | none | minio |
| app | ListBucket | any | deny | AccessDenied | 60s | none | mc AccessDenied | none | minio |
| app | GetObject | any | deny | AccessDenied | 60s | none | mc AccessDenied | none | minio |
| app | PutObject | any | deny | AccessDenied | 60s | none | mc AccessDenied | none | minio |
| app | DeleteObject | any | deny | AccessDenied | 60s | none | mc AccessDenied | none | minio |
| retention-admin | policy structure | canary only | validated | — | — | — | validate-policies OK | — | offline |
| retention-admin | PutObjectRetention | `canary/<RUN_ID>/` | (provisional) | — | — | — | not applied in 3C2A | — | future-selectel |

## Future Selectel rows — NOT EXECUTED IN 3C2A (contract for 3C2C)

| check | operation | expected | tier |
|---|---|---|---|
| wrong endpoint | any | connection refused / not routed | future-selectel — NOT EXECUTED IN 3C2A |
| wrong region | any | region mismatch error | future-selectel — NOT EXECUTED IN 3C2A |
| revoked/expired key | any | AccessDenied / InvalidAccessKeyId | future-selectel — NOT EXECUTED IN 3C2A |
| malformed Signature V4 | any | SignatureDoesNotMatch | future-selectel — NOT EXECUTED IN 3C2A |
| path-vs-vhost addressing | any | correct style resolves, other fails/redirect not silent | future-selectel — NOT EXECUTED IN 3C2A |
| Object Lock bypass | DeleteObjectVersion locked | AccessDenied unless BypassGovernance (admin only) | future-selectel — NOT EXECUTED IN 3C2A |
| eventual visibility | List after Put | visible within bound | future-selectel — NOT EXECUTED IN 3C2A |
| Selectel request-IDs | all | request-id captured in evidence (no secrets) | future-selectel — NOT EXECUTED IN 3C2A |
| credential rotation | old key after rotate | AccessDenied | future-selectel — NOT EXECUTED IN 3C2A |
| pgBackRest closure | archive/backup/check without Delete | proves whether Delete is truly required | future-selectel — NOT EXECUTED IN 3C2A |
