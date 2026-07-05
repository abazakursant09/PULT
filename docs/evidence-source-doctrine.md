# PULT Evidence Source Doctrine

> Normative architecture doctrine. Refines **evidence sourcing** only. It sits under the
> [canonical-surface doctrine](canonical-surface-doctrine.md) and the
> [pult-system doctrine](pult-system-doctrine.md) and does **not** change the advisory-vs-execution
> split.

---

## 1. Core model — Evidence is dual-source

PULT diagnosis is fed by **Evidence**, and Evidence has exactly **two sources**:

```
Uploaded Report Evidence   +   API Snapshot Evidence
```

Both sources write **append-only, dated, provenance-tagged Evidence tables**. Diagnosis reads
those database tables **only**, and is **source-agnostic** — it neither knows nor cares which
source produced a row.

### Flow

```
INGESTION  (outside the Advisory Runtime; no producer, no orchestration)
├── Upload Evidence
│     └── csv_import → parser → confirm → persist
└── API-Snapshot Evidence
      └── scheduled read-only pull → persist
                          ↓   (both write the SAME append-only Evidence tables)
                      DATABASE      (immutable, dated, provenance-tagged rows)
                          ↓
                    DIAGNOSIS        (flat producers; read DB only; source-agnostic)
                          ↓
                     SIGNALS
                          ↓
                  DECISION FEED
                          ↓
                   EXECUTION
```

The diagnosis layer, the signal tables, the Decision Feed, and Execution are **unchanged**. Only
the *ingestion frontier* gains a second sibling (API-Snapshot) alongside the existing file-upload
path.

---

## 2. The three principles

### A. Evidence Source Symmetry

Upload and API Snapshot are two ways to produce the **same evidence row shape**. A contour **must
not know or care** which source produced a row. There is one read contract — the Evidence table —
not one contract per source. This is what keeps the number of architectural concepts minimal and
the diagnosis layer decoupled from origin.

### B. Marketplace API has two disjoint uses

- **Use 1 — Execution:** write, user-confirmed, the Closed Loop (e.g. `set_price`, `publish_review_response`).
- **Use 2 — Evidence Snapshot:** read-only, scheduled ingestion into Evidence tables.

**Never mix them.**

- The **Executor is never a diagnosis input.**
- The **Snapshot reader never writes.**

They are separate capabilities of the marketplace clients, kept physically distinct so an
execution path can never leak into an evidence path and vice versa.

### C. Evidence is immutable, dated, and provenance-tagged

- API snapshots are **append-only** — never updated in place.
- Each row carries a `captured_at` / `observed_at` (or equivalent) timestamp and a `source` tag.
- **Mutating marketplace state is represented as successive snapshots**, never as an in-place
  update.
- Diagnosis chooses **latest-by-date** or **window-by-date** over the snapshot series.

This is precisely what preserves **replay** and **deterministic diagnosis**: re-running a diagnosis
over the same immutable Evidence rows yields the same result.

---

## 3. Preserved invariants

The revision is purely additive; every existing invariant holds by construction:

- **Runtime remains flat.**
- **Diagnosis never calls the Marketplace API directly.**
- **Diagnosis reads the DB only.**
- **No producer dependencies.**
- **No orchestration.**
- **Deterministic diagnosis.**
- **Replayable evidence.**
- **Advisory-only before execution.**
- **API ingestion is an ingestion sibling of `csv_import` — NOT an Advisory Runtime producer.**

The last point is the load-bearing one: because the API-Snapshot pull runs *outside* the flat
diagnosis runtime (on its own schedule, like the file-upload confirm), it adds **zero** producer
dependencies and **zero** orchestration to the diagnosis path.

---

## 4. Updated doctrine statements

Replace the old simplified statements:

> ~~Marketplace API → execution only~~
> ~~Reports → diagnosis~~

with:

> **Marketplace API → Execution OR Evidence Snapshot** (two disjoint uses, never mixed)
> **Uploaded Reports → Evidence**
> **Diagnosis → DB-only** (source-agnostic over Evidence tables)

---

## 5. Per-contour source map

### Report / upload-driven — unchanged

Finance, sales, product snapshots, and returns are report-primary on every marketplace, so these
stay upload/report-driven:

- Revenue
- Money Leak
- Supply
- Rating
- Review Velocity
- Overstock
- Price Erosion
- Returns rate

### API-snapshot driven

No downloadable-report equivalent exists — these are only sourceable via API snapshot:

- SEO category schema
- SEO constraints
- Required attributes
- Search Position
- Live Card State
- Structured return reasons (where the marketplace exposes them)

### Combined

- **SEO** = card content from **upload OR API snapshot** + category schema/constraints from **API snapshot**
- **Returns** = return **rate** from **uploaded reports** + reason-code enrichment from **API snapshot**
- **Advertising** = finance **spend** from **reports** + campaign/bid **state** from **API snapshot**

---

## 6. Unchanged implementation

This doctrine changes **no existing code**. The following remain exactly as-is:

- Advisory Runtime
- all 14 flat producers
- existing `diagnosis_source` implementations (still DB-only)
- Decision Feed
- executor / Closed Loop
- `csv_import` path
- `evidence_hash` discipline
- honest `not_evaluated` behavior
- all 8 live contours

---

## 7. Future implementations made simpler

Immutable API snapshots simplify each of these:

- **SEO** — `build_snapshot_from_import` reads Evidence tables (card content + category schema); the
  SEO engine already exists, so the producer becomes a thin adapter. The long-standing *constraints
  gap* closes: category schema/required-attributes arrive as API-snapshot Evidence instead of being
  permanently unavailable.
- **Search Position** — a pure API-snapshot Evidence table plus the standard 5-slice playbook; no
  report parsing at all. Successive dated snapshots give an honest position time-series.
- **Orders / Fulfillment** — the earlier blocker (mutating order status breaks the append-only
  model) **dissolves**: status becomes successive immutable snapshots, and diagnosis reads
  latest-by-date. Determinism is preserved with no special-casing.
- **Reason-coded Returns** — return reasons land as API-snapshot Evidence and enrich the existing
  (already-live) upload-driven Returns rate, without disturbing the frequency-only, double-count-safe
  rate diagnosis.

---

## 8. Hierarchy

This doctrine sits under:

- [canonical-surface doctrine](canonical-surface-doctrine.md)
- [pult-system doctrine](pult-system-doctrine.md)

It **refines evidence sourcing only**. It does **not** change the advisory-vs-execution split, the
executable-vs-advisory surface rule, or any signal/contour semantics.

---

## One-line standard

> **Evidence is dual-source (Upload + API-Snapshot), immutable, dated, and DB-resident. Diagnosis
> reads Evidence tables only and cannot tell the sources apart. The Marketplace API is used two
> disjoint ways — Execution (write) and Evidence Snapshot (read) — never mixed. The flat,
> deterministic, replayable diagnosis runtime is untouched; the API becomes an ingestion source,
> never a diagnosis-time dependency.**
