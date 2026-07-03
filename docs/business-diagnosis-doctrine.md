# PULT Business Diagnosis Doctrine

> **Umbrella mission doctrine — the philosophical foundation of PULT.**
> This document states *why* PULT exists and *how it must reason*. It sits **above**
> the structural doctrines and does **not** replace them:
>
> - [`pult-system-doctrine.md`](./pult-system-doctrine.md) — the two surfaces
>   (Executable vs Advisory) through which diagnosis and treatment are realized.
> - [`canonical-surface-doctrine.md`](./canonical-surface-doctrine.md) — the gate
>   deciding whether a use-case may be Executable.
> - [`learning-key-doctrine.md`](./learning-key-doctrine.md) — the Learning OS unit
>   and aggregation key.
>
> A PR that conflicts with this document must be rejected. **This document changes no
> code and no runtime** (see §7). It is a decision filter for everything PULT builds
> next, not a new component.

## 1. Definition — PULT is a Business Diagnosis operating system

PULT is **not** BI, **not** CRM, **not** analytics, **not** reports.

PULT is a **business-diagnosis operating system for marketplace sellers.**

PULT does not merely display data. PULT **diagnoses** observed business problems, shows
the **evidence**, explains the **likely root cause**, prescribes a **treatment**, states
the **expected effect**, measures the treatment's **result**, and **learns** from
confirmed outcomes.

PULT diagnoses the **observed present**. It never prognoses the future.

## 2. Canonical reasoning chain

```
Symptoms → Evidence → Diagnosis → Root Cause → Treatment → Expected Effect → Learning
```

This is a **philosophical and read-layer** reasoning chain. It is **not** a new runtime
layer and introduces **no orchestration**. It renames what PULT already does:
Producer → Signal → Feed correlation → Action contour → Closed Loop → Learning OS.

### Doctrine rules

1. **Symptoms.** A symptom is an *observed sign that something is wrong or underused* —
   revenue decline, margin erosion, stock risk, ad waste, rating/reputation
   deterioration, legal/content risk, underused growth potential.
2. **Evidence.** Every claim must be grounded in **observed seller data**. *No evidence
   means no claim.* Evidence is **not** a new runtime component, service, or table — it
   is a **discipline every signal must follow** (the observed-only snapshot each producer
   already builds).
3. **Diagnosis.** Diagnosis **localizes** the problem — **per product, per marketplace,
   per problem.** PULT must **never** create a single global "business health score."
4. **Root Cause.** Root cause is **evidence-bounded**. PULT may say *"likely cause"* or
   *"co-occurring signals indicate."* PULT must **not** present correlation as proven
   causation. Root-cause reasoning lives at the **read/synthesis layer**, never as a
   producer or a runtime orchestrator.
5. **Treatment.** Treatment belongs to the existing **action contours** — Pricing,
   Advertising, Operations, Review, Legal. **Diagnosis must not absorb action
   ownership.**
6. **Expected Effect.** Expected effect is **qualitative** or bounded by existing
   measurement logic. PULT must **not** fabricate forecasted numbers. Real effect is
   confirmed **only after measurement.**
7. **Learning.** Learning records **which treatments actually resolved which diagnoses.**
   Future ranking and recommendations improve from **confirmed outcomes**.

## 3. Why this makes PULT different from BI

A BI dashboard **presents data and leaves interpretation to the human** — it never
diagnoses, treats, or learns. A "health score" is a **number on a gauge**.

A diagnostician **interprets evidence, names a likely cause, prescribes a treatment,
states the expected effect, and revises from the outcome.**

> Health score is a chart. Diagnosis is a doctor.

Because "Diagnosis" is an **act** (reason-to-action), not a **state** (a readout), it is
both the truer name for PULT's mission and its strongest guardrail: every future product
question — *"add a KPI? a trend chart? a dashboard widget?"* — is answered by *"does a
diagnosis require it?"* The answer is almost always no.

## 4. Mapping existing contours into the doctrine

The doctrine **describes the system already built** — every contour fits with no runtime
change. The current contours are each a *localized diagnosis fused with a treatment*
(they detect one problem and own its fix). The future Business-Diagnosis tier adds the
*systemic diagnosis* (revenue/margin trajectory) that gives those local treatments
purpose and priority.

| Contour | Role in the doctrine | Evidence (observed only) | Treatment |
|---|---|---|---|
| **Advertising** | localized Diagnosis + Treatment | finance ad_spend / net_profit / DRR | `ad_set_state` (executable) |
| **Pricing** | localized Diagnosis + Treatment | finance margin / `PricingRule.min_price` | `set_price` (executable) |
| **Operations** | localized Diagnosis + Treatment | `ImportedProductRow.stock`; auto-promo participation + net_profit | replenish (advice) / `stop_auto_promotion` (executable) |
| **Review** | localized Diagnosis + Treatment | `ReviewResponse` (owned via Product) | manual reply (MANUAL_ONLY) |
| **Legal** | localized Diagnosis + Treatment | catalog claim text | review claim (AUTO_FORBIDDEN, advice) |
| **Growth** | Diagnosis of **latent capacity** (money not yet earned) + Treatment | finance profit / margin / cross-contour signals | start-advertising / improve-listing (advice) |
| **Revenue Diagnosis** *(future)* | **pure systemic Diagnosis — WHERE** money is disappearing | finance revenue time-series (confirmed trend, spike-rejected) | none — routes to a Treatment contour |
| **Money Leak Detection** *(future)* | **pure systemic Diagnosis — WHY** (silent cost-structure drift) | finance commission / logistics / net_profit deltas over time | none — routes to a Treatment contour |

Two facts this mapping proves: **Growth shows diagnosis covers latent capacity, not only
pathology**; and the future systemic diagnoses (Revenue, Money Leak) are **pure Diagnosis
with no treatment of their own** — they localize loss and route to the contours that own
the fix.

## 5. Explicit anti-patterns

Reject any PR that introduces:

- a **business health score** (single global number);
- a **KPI dashboard**;
- **charts as a product goal**;
- a **claim without evidence**;
- a **root cause without observed support** (or correlation presented as proven cause);
- **prognosis / fabricated forecast** (predicted numbers before measurement);
- **runtime orchestration** (diagnosis routing/coupling inside the runtime);
- a **new source of truth** parallel to the canonical chain.

## 6. Architectural guardrails (binding)

- Do **not** redesign the runtime. Do **not** introduce runtime orchestration.
- Keep the runtime **flat and decoupled** — a producer is one `ProducerSpec(key, run,
  cadence, enabled)`; the runtime knows no contour relationships, no priority, no
  dependencies.
- Keep the canonical chain intact:

  ```
  Producer → Advisory Runtime → Signal Tables → Decision Feed → Today
           → Dashboard / Telegram / Copilot
  ```

- **Business Diagnosis is a philosophical and read-layer doctrine, not a new runtime
  layer.**
- **Evidence is a discipline, not a new service or table.**
- **Root Cause Analysis lives at the read / synthesis layer** (Decision Feed / Today
  correlation), never as a producer or orchestrator.
- **Producers remain flat peers.** Diagnosis producers (future Revenue / Money Leak) are
  registered exactly like every other contour.

## 7. This doctrine requires no code or runtime change

This is a doctrine reframing that the current system **already satisfies**. It renames the
existing pipeline under truer terms; it adds no component, no table, no orchestration, no
source of truth. Adopting it changes **no production code and no runtime behavior** — its
value is as a normative decision filter for future work.
