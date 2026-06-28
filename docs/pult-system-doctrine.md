# PULT System Doctrine

> **Umbrella normative document.** This is the top-level frame for PULT's surfaces.
> It sits **above** the more specific doctrines and does **not** replace them:
>
> - [`canonical-surface-doctrine.md`](./canonical-surface-doctrine.md) — decides
>   whether a use-case may be Executable (the gate).
> - [`learning-key-doctrine.md`](./learning-key-doctrine.md) — the Learning OS unit
>   and aggregation key.
> - [`canonical-spine-consolidation-audit.md`](./canonical-spine-consolidation-audit.md)
>   — Spine consolidation rules and open legacy↔canonical zones.
>
> A PR that conflicts with this document must be rejected. This document changes no
> runtime (see §7).

## 1. Purpose

PULT consists of **two first-class, equal surfaces**:

- **Executable Surface**
- **Advisory Surface**

Neither is superior. The Advisory Surface is **not** an unfinished Executable Surface
— absence of an Apply step is its **correct terminal shape**, not a deficiency. Each
surface has its own complete lifecycle and its own definition of "done."

## 2. Executable Surface

Lifecycle:

```
Signal → Decision → Apply → Measure → Effect → Learning
```

**Назначение:** изменение состояния marketplace (a real write through a marketplace
API). The lever is a machine action with an honestly-derived, observed payload. A
contour/use-case may enter this surface only by satisfying every criterion of the
Canonical Surface Doctrine (§5).

**Done** = the action was applied and its observed effect was measured and learned.

## 3. Advisory Surface

Lifecycle:

```
Signal → Diagnosis → Evidence → Recommendation → Human Decision → (optional Measure)
```

**Назначение:** повышение качества решений продавца (improve the quality of the
seller's own decisions). The terminal step is a **human decision** — the human carries
the judgement and the responsibility. There is no executor and no machine Apply, and
that is correct.

Normative ordering: **Evidence MUST precede Recommendation.** PULT shows the observed
facts first and only then what the seller might consider — a recommendation is never
presented ahead of the evidence that grounds it.

- **Signal** — an observed fact (same observed-only rule as Executable: no forecast,
  no AI, no competitor data, no fabricated number).
- **Diagnosis** — a deterministic interpretation of the observation (what it means).
- **Evidence** — the observed facts the diagnosis stands on (for trust and audit).
- **Recommendation** — what the seller might consider. **Not** an executable payload,
  **not** authored content presented as an action.
- **Human Decision** — the terminal step. The human acts (or chooses not to). This is
  the Advisory Surface's success.
- **(optional) Measure** — if the human's action produces an observable metric, it may
  be measured, but see §6 for the hard attribution boundary.

**Done** = the seller was handed a grounded, evidence-first recommendation and a
decision. No Apply is required or expected.

## 4. Classification

Surface class is assigned **per contour** (its primary nature), independent of whether
the contour happens to host an isolated executable use-case.

| Contour | Class |
|---|---|
| **Pricing** | Executable |
| **Advertising** | Executable |
| **Operations** | Advisory contour **with an executable use-case** |
| **SEO** | Advisory |
| **Reviews** | Advisory |
| **Legal** | Advisory |
| **Growth** | Advisory |
| **Finance** | Advisory |
| **Warehouse / Logistics** | Advisory |

**"Mixed" is NOT a third class.** It is an **Advisory-class contour that contains one
or more executable use-cases**. A single executable use-case does not change the
contour's class. (Operations is the canonical example: an advisory contour whose
auto-promotion margin-drain use-case is executable.)

## 5. Relationship to the Canonical Surface Doctrine

This document defines the **two surfaces**. The
[`canonical-surface-doctrine.md`](./canonical-surface-doctrine.md) is the
**subordinate gate**: it decides whether an Advisory use-case may cross into the
Executable Surface — i.e. whether all six Executable criteria (observed Signal,
observed-derived payload, honest write/unavailable, Measure, Effect, Learning) hold.

- System Doctrine = the frame (which surface a contour belongs to, and each surface's
  lifecycle).
- Canonical Surface Doctrine = the test (whether a specific use-case earns Executable
  status).

A contour staying Advisory is a **valid, complete outcome of the gate**, never a
failed Executable.

## 6. Learning

Normative:

- **Learning OS belongs to the Executable Surface only.** It aggregates the observed
  effect of executable **levers**, keyed per
  [`learning-key-doctrine.md`](./learning-key-doctrine.md) on
  `(marketplace, action_key, metric_key)`.
- **The Advisory Surface does not write lever outcomes into Learning OS.** There is no
  executor `action_key` for a human decision, so an advisory outcome has no place in
  the executable Learning key space.
- **If an Advisory contour ever has its own metrics, they MUST NOT be mixed into the
  Learning key.** Any advisory measurement is attributed to the human decision, kept in
  a distinct, clearly-typed space, and never pooled with executable lever learning.

This protects the integrity of `(marketplace, action_key, metric_key)`: every count in
a Learning bucket is the observed effect of a **machine lever**, never a
human-attributed outcome.

## 7. Runtime

This document is **normative only**. It changes **no**:

- runtime;
- schema;
- DTO;
- Decision Spine (`Decision`, `EngineSignalDecisionLink`, `EngineEffectObservation`);
- Learning implementation.

The Learning-attribution boundary in §6 is already true in practice (advisory contours
write no executor-keyed observations); this document records it, it does not introduce
it.

## 8. Related documents

- **Down →** [`canonical-surface-doctrine.md`](./canonical-surface-doctrine.md) — the
  Executable gate (§5).
- **Down →** [`learning-key-doctrine.md`](./learning-key-doctrine.md) — the Learning
  key, scoped to the Executable Surface (§6).
- **Context →**
  [`canonical-spine-consolidation-audit.md`](./canonical-spine-consolidation-audit.md)
  — Spine consolidation rules; its open legacy↔canonical zone is an
  Advisory-surface vs Executable-surface question for the same problem.
