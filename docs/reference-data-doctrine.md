# PULT Reference Data Doctrine

> **Amendment** to the [Evidence Source Doctrine](evidence-source-doctrine.md) — it **extends, does
> not rewrite** it. Introduces **Reference Data** as a distinct third source class. Sits under the
> [canonical-surface doctrine](canonical-surface-doctrine.md) and the
> [pult-system doctrine](pult-system-doctrine.md); it refines **sourcing only** and does not change
> the advisory-vs-execution split.

The Evidence Source Doctrine is correct but **incomplete**: it modelled two sources (Uploaded Report,
API Snapshot) both as *Evidence*. Some marketplace information is **not** evidence — it is
**Reference Data**, with different owner, semantics, and lifecycle. This doctrine names it.

---

## 1. Definition — Reference Data

**Reference Data is a fact about the marketplace/world, true independently of any seller, whose
value to diagnosis is the version *currently in effect* — not its change history.**

- **global** — one value per `(marketplace, …)`, shared across all sellers
- **marketplace-owned** — describes the environment, not a seller's business
- **no `user_id`**
- **versioned current-state** — latest authoritative version supersedes; history kept only for replay
- **not business observations** — a rule/directory/schema, never "what happened to my business"
- used as **marketplace rules and constraints** for diagnosis

Examples: category tree · category schema · required attributes · allowed values · attribute
dictionaries · moderation rules · marketplace constraints · commission tables · tariff tables ·
warehouse directory.

**Discriminating test:** *Does the datum's change-over-time carry business meaning for the
diagnosis?* No (only the current value matters) → **Reference**. Yes (the timeline is the signal) →
**Evidence**.

---

## 2. The three source classes

| | Source Class A | Source Class B | Source Class C |
|---|---|---|---|
| Name | **Uploaded Report Evidence** | **API Snapshot Evidence** | **Reference Data** |
| Owner | seller (`user_id`) | seller (`user_id`) | marketplace (**global, no user_id**) |
| Nature | per-seller observation | per-seller observation | global rule / directory |
| Timeline | append-only, IS the signal | append-only, IS the signal | versioned current-state, NOT a signal |
| Origin | `csv_import → parser → confirm → persist` | scheduled read-only API pull | scheduled read-only API pull |

**Evidence is per seller. Reference is marketplace-global.** A and B are the two Evidence sources
from the Evidence Source Doctrine; C is the new peer.

---

## 3. Architecture (the correct shape)

```
Sources
├── Uploaded Report Evidence   (per-seller, append-only timeline)
├── API Snapshot Evidence      (per-seller, append-only timeline)
└── Reference Data             (global, versioned current-state, replay-pinned)
        ↓
Snapshot / Aggregate Build      (merges Evidence + Reference into ONE enriched input)
        ↓
Diagnosis                       (reads DB only; source-agnostic)
        ↓
Signals
        ↓
Decision Feed
        ↓
Execution
```

### Explicitly REJECTED

```
Reference → Evidence → Diagnosis      ✗  WRONG
```

**Reference is NOT transformed into Evidence.** Forcing global rules through the per-seller evidence
pipeline mislabels them (fake `user_id`, per-seller duplication). **Reference and Evidence are
peers** — neither is upstream of the other. They meet only at the build step.

---

## 4. Reference semantics

- **latest-wins** — diagnosis uses the version in effect
- **versioned current-state** — a new authoritative version supersedes the prior one
- **immutable stored versions** — versions are never mutated in place
- **replay uses the pinned version** — a re-run reconstructs the exact version the original run used
- **history exists for reproducibility, NOT because rule history is diagnostic** — when a marketplace
  changed a rule is not a seller symptom

This is **current-state with version history for replay** — deliberately NOT the append-only
timeline semantics of Evidence (where the series itself is the diagnostic input).

---

## 5. Snapshot / Aggregate Build — the single merge point

**Reference merges with Evidence ONLY during Snapshot / Aggregate Build.** The builder composes
per-seller Evidence + global Reference into one enriched input (e.g. a `CardSnapshot` whose content
fields come from Evidence and whose `constraints` / `category_schema` come from Reference).

- **Diagnosis never receives two separate inputs.**
- **Diagnosis remains source-agnostic** — it reads one DB-resident enriched input and cannot tell
  Upload from API-Snapshot from Reference.
- Reference therefore needs distinct **storage** and **semantics**, but **NOT** a distinct
  diagnosis-consumption stage.

---

## 6. Replay rule (new obligation)

**Every diagnosis that consumes Reference must pin the reference version it used** —
`reference_version_id` (or reference `captured_at` / version). Without the pin, re-running a
diagnosis after a rule change (e.g. a commission update) would yield a different answer and break
replay. The pin is the price of correct determinism when Reference participates.

---

## 7. Freshness rule (new obligation)

Reference requires what Evidence does not:

- a **refresh cadence** (low — schemas/tariffs change rarely: weekly/monthly)
- a **freshness timestamp** (`captured_at`)
- a **staleness guard** — a stale commission/tariff/schema silently corrupts diagnosis, so a too-old
  Reference version must degrade honestly (rules → `not_evaluated`) rather than diagnose on stale
  rules

Evidence has no freshness SLA (an old observation is still a true observation); Reference does.

---

## 8. Global fetch via per-seller auth

Reference data is **global**, but the marketplace API that serves it authenticates **per seller**.
So ingestion uses **one seller's token to fetch data for all** and **deduplicates by
`(marketplace, category_id, …)`**. The fetched rows carry no `user_id`.

---

## 9. Canonical entity classification

Four classes: **Evidence** (per-seller observations) · **Reference** (global marketplace facts) ·
**Execution** (the write / Closed-Loop side) · **System** (PULT-internal derived / orchestration).

| Entity | Class |
|---|---|
| Revenue | Evidence-driven diagnosis |
| Money Leak | Evidence-driven diagnosis |
| Supply | Evidence-driven diagnosis |
| Rating | Evidence-driven diagnosis |
| Review Velocity | Evidence-driven diagnosis |
| Overstock | Evidence-driven diagnosis |
| Price Erosion | Evidence-driven diagnosis |
| Returns | Evidence-driven diagnosis |
| Card Content | **Evidence** (per-seller uploaded card state) |
| Search Position | **Evidence** (per-seller rank over time; API-snapshot sourced) |
| Category Schema | **Reference** |
| Required Attributes | **Reference** |
| Marketplace Constraints | **Reference** |
| Commission Tables | **Reference** |
| Tariffs | **Reference** |
| Warehouse Directory | **Reference** |
| CardSnapshot | **System** (derived build product — merges Evidence + Reference) |
| Decision | **System** |
| Signal | **System** |
| Feed | **System** |
| Learning | **System** |
| ExecutionLog | **Execution** |

(The 8 contour names are diagnoses; their *input* class is Evidence.)

---

## 10. Unchanged

This amendment extends **sourcing only**. Confirmed unchanged:

- Runtime (flat)
- Producers (all flat, DB-only)
- Diagnosis
- Decision Feed
- Executor
- Closed Loop
- the Evidence Source Doctrine (extended, not rewritten)
- all 8 LIVE contours

Every invariant the Evidence Source Doctrine guarantees still holds — flat runtime, diagnosis never
calls the API, DB-only diagnosis, no producer dependencies, no orchestration, replayable,
advisory-only. Reference ingestion, like API-Snapshot Evidence, is an **ingestion sibling of
`csv_import`, NOT an Advisory Runtime producer**.

---

## 11. Roadmap correction (C2)

- **C2b** becomes **global Reference tables** — no `user_id`, versioned current-state,
  provenance-tagged (`captured_at`, version).
- **C2d** must **pin the reference version** used during SEO evaluation, and honor the freshness /
  staleness guard.

---

## One-line standard

> **There are three source classes: Uploaded Report Evidence and API Snapshot Evidence (per-seller,
> append-only timeline) and Reference Data (global, versioned current-state). They are peers that
> merge only at Snapshot / Aggregate Build into one enriched, DB-resident input; Diagnosis stays
> source-agnostic. Reference is never transformed into Evidence. A diagnosis using Reference pins the
> version it used (replay) and honors a freshness guard (staleness). The flat, deterministic
> diagnosis runtime is untouched.**
