# RFC: Marketplace Execution Layer (ME)

**Status:** DRAFT — awaiting approval. No implementation until approved.
**Sprint track:** ME-1 (Execution Foundation) → first vertical slice ME-2 (Reviews Auto Publish).
**Governs to:** `docs/governance/product_direction_lock_constitution.md`,
`docs/governance/sprint_acceptance_gate_constitution.md`,
`docs/governance/schema_governance.md`,
`docs/governance/operational_review_constitution.md`.

---

## 1. Problem

PULT today is L1–L2: import → analytics → insight → recommendation. There is **no
marketplace integration** (no WB/Ozon Seller API). "Execution" is an illusion:
pricing updates the local DB only, reviews flip a local status only, SEO/ads stop
at recommendations.

This violates the Product Direction Lock: `Automation > Execution > Recommendation
> Analytics`. The product chain `Problem → Cause → Solution → Execution →
Automation → Result` breaks at **Execution**.

## 2. Goal / Non-goals

**Goal.** A single, audited, reversible layer through which every seller action
reaches the real marketplace. L3 (one-click execute) first, L4 (automation) on
top of the same path.

**Non-goals (this RFC).**
- No new dashboards/reports/analytics screens (forbidden by the Lock unless an
  action ships with them).
- No change to frozen layers (`backend/logic/` per Sprint 71/72,
  `operational_review`, `runtime_*`).
- No business logic for *which* action to take — that stays in Action Engine.
  This layer only *executes* a decided action.

## 3. Position in the stack

```
Action Engine (decides action + payload)        ← existing, L2
        │  action_type + payload
        ▼
Marketplace Execution Layer  ──────────────────  ← THIS RFC
   marketplace_executor.execute()  (single entry)
   ├─ action_catalog   (action_type → client method + validator + reverter)
   ├─ guard            (margin floor, max step, daily cap, negative-never-auto)
   ├─ credential_vault (encrypted tokens)
   ├─ wb_client / ozon_client (HTTP)
   └─ ExecutionLog     (append-only audit)
        │
        ▼
Automation Engine (L4)  ── rules → scheduler → same execute()  ← ME-7
```

Execution and Automation share **one** code path: L4 is L3 invoked by a rule
instead of a user. This is the core design choice — no second execution engine.

## 4. Data model (new tables — Alembic migration required, per schema_governance)

- **`marketplace_connections`** — one per seller cabinet. `id, user_id,
  marketplace, label, status(connected|invalid|revoked), scopes[], ozon_client_id,
  last_check_at, timestamps`.
- **`api_credentials`** — tokens, separated from connection, encrypted at rest.
  `id, connection_id, scope, secret_enc(bytes), meta, expires_at`. Secret never
  stored or logged in plaintext.
- **`execution_logs`** — append-only audit. `id, user_id, connection_id,
  insight_key?, action_type, mode(manual_l3|automated_l4), payload(no secrets),
  api_request_id?, status(pending|running|success|failed|reverted), result,
  reverted_from?, created_at, finished_at`.
- **`automation_rules`** (ME-7, defined now for shape) — `id, user_id, contour,
  action_type, trigger(json), guard(json), mode(confirm|auto), enabled`.

All new modules under `backend/models/`; each model change ships with its Alembic
migration in the same change set (schema_governance §constitutional requirement).

## 5. Executor contract — `marketplace_executor.execute()`

This is the contract under review. Implementation deferred.

### 5.1 Signature
```python
async def execute(
    *,
    db: AsyncSession,
    user_id: str,
    action_type: str,            # e.g. "publish_review_response"
    payload: dict,               # action-specific, validated by action_catalog
    mode: str = "manual_l3",     # "manual_l3" | "automated_l4"
    connection_id: str | None = None,  # resolved from user+marketplace if None
    insight_key: str | None = None,    # provenance: which insight triggered this
    idempotency_key: str | None = None,
    dry_run: bool = False,
) -> ExecutionResult: ...
```

### 5.2 ExecutionResult
```python
@dataclass(frozen=True)
class ExecutionResult:
    log_id: str
    status: str                  # success | failed | rejected | dry_run_ok
    action_type: str
    marketplace: str
    api_request_id: str | None
    result: dict                 # normalized API response (secrets stripped)
    error: ExecutionError | None
    reversible: bool
    revert_hint: dict | None     # payload to pass to revert() to undo
```

### 5.3 Pipeline (every call, in order)
1. **Resolve** connection (user + action_type→marketplace). Missing/invalid →
   `rejected: NO_CONNECTION`.
2. **Scope check** — connection must hold the API scope the action needs
   (`feedbacks`, `prices`, `advert`, `content`, `stocks`, `promotions`). Missing →
   `rejected: MISSING_SCOPE`.
3. **Validate** payload via `action_catalog[action_type].validator`. Bad → raise
   `ExecutionError(VALIDATION)`. Never partial-send.
4. **Guard** (centralized, before any network):
   - margin floor (price/discount actions),
   - max step per action (e.g. bid ±X%),
   - daily action cap per user/action_type,
   - **negative reviews are never auto-published** (hard rule),
   - `mode=automated_l4` requires an enabled `AutomationRule` whose guard passed.
   Fail → `rejected: GUARD_<reason>`, logged, no network.
5. **Log pending** — write `ExecutionLog(status=pending)` BEFORE the call (so a
   crash mid-call is visible).
6. **Dispatch** — `action_catalog[action_type]` → wb_client/ozon_client method.
   Credentials fetched from vault at call time, never held in payload/log.
7. **Normalize + persist** — update log to `success|failed`, store
   `api_request_id`, normalized result, `finished_at`.
8. **Return** `ExecutionResult`.

`dry_run=True` runs steps 1–4 (+ build request) and returns `dry_run_ok` without
dispatch — used by L4 rule preview and tests.

### 5.4 Guarantees
- **Single entry.** No router or task calls a marketplace client directly. Only
  `execute()` does. (Enforced by review + lint rule later.)
- **Idempotent.** `(user_id, action_type, idempotency_key)` dedupes within a
  window; replays return the prior log, not a second API call.
- **Reversible where the API allows.** `reversible=True` actions populate
  `revert_hint`; `await revert(db, log_id)` issues the inverse and links
  `reverted_from`. Price/bid/stock/promotion = reversible; review publish = not.
- **Audit-first.** Append-only `execution_logs`; failures are logged, not
  swallowed. (Aligns with operational_review append-only discipline; this layer
  is NOT under the review boundary — it has execution authority by design, but
  borrows the append-only audit posture.)
- **Secret-safe.** Tokens only in `credential_vault` (encrypted, env-held key).
  Never in payload, result, log, or exception text.

### 5.5 Error taxonomy
```
ExecutionError(code, retryable, detail)
  VALIDATION        not retryable
  NO_CONNECTION     not retryable
  MISSING_SCOPE     not retryable
  GUARD_<reason>    not retryable
  AUTH              not retryable (token invalid → mark connection invalid)
  RATE_LIMIT        retryable (backoff)
  MARKETPLACE_5XX   retryable
  MARKETPLACE_4XX   not retryable (bad request to API)
  TIMEOUT           retryable
```

## 6. Security

- Token encryption: AES-GCM / Fernet, key from env (`CRED_ENC_KEY`), never in DB
  or repo. Rotation supported via `api_credentials.meta`.
- Connection verification on save (cheap read call) → `status`.
- Least scope: store only scopes the user granted; guard refuses out-of-scope.

## 7. First vertical slice — ME-2 Reviews Auto Publish

Approved scope after this RFC is signed off:

- `action_type = "publish_review_response"`, marketplace WB (Feedbacks API).
- **L3:** Action Engine `rating_good` insight surfaces drafted answers (existing
  `generate_review_responses`) → user clicks Execute → `execute()` →
  WB Feedbacks publishes → `ReviewResponse.status="published"` set **after** real
  API success (today it's set with no API call — that gets fixed here).
- **L4:** `AutomationRule(contour=reputation, action_type=publish_review_response,
  trigger={rating>=4}, guard={negative_never:true}, mode=auto)` → scheduler →
  `execute(mode=automated_l4)`. Negative reviews never auto-publish; they produce
  a draft + operator escalation only.
- Touches: new ME tables, `wb_client.feedbacks`, `marketplace_executor`,
  `routers/execution.py`, rewire `routers/reviews.py` PATCH to call `execute()`.
- Out of scope for the slice: Ozon reviews (premium, separate), other contours.

Why this slice first: daily rutина, WB Feedbacks API is simple, negative-guard
makes L4 safe earliest — max "PULT does it for me" at min money risk. Proves the
shared L3/L4 path end-to-end before pricing/ads (which carry money risk).

## 8. Enabler / Activation (per sprint_acceptance_gate)

- ME-1 is an **Enabler**: `Enabler(execution foundation) → Feature(reviews) →
  Action(publish L3) → Automation(auto-positive L4)`.
- Activation Status: ME-1 = `Pending` until ME-2 ships the L3/L4 review path, then
  `Activated`. Target activation Sprint ID = **ME-2** (sequence ordinal, no
  wall-clock — per operational_review no-clock discipline).

## 9. Open questions (decide before / during ME-1)

1. Queue tech for L4 scheduler — APScheduler (in-proc) vs Celery (separate worker)?
   Affects deploy. Recommend APScheduler for slice, revisit at ME-7.
2. `CRED_ENC_KEY` provisioning in this env (no secrets manager visible) — env file
   vs OS keyring?
3. WB Feedbacks token scope granularity — confirm single token covers
   list+answer.
4. business-pult is **not under git** — version control before writing code?
   (Risk: ME work unrecoverable.)

## 10. Approval

On sign-off: implement ME-1 foundation + ME-2 vertical slice (Reviews Auto
Publish, L3 then L4) per §7. Nothing implemented before that.
