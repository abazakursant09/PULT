# Sprint Acceptance Gate Constitution

**Status: ACTIVE GOVERNANCE.** Defines when a sprint is *complete*. Product-level
gate, not a per-module rule. Layered scope (see §2) keeps it compatible with the
frozen descriptive-only contracts — no governance unlock required.

This document **references** existing constitutions; it does not restate their
logic. See the reference map in §8.

---

## 1. Core principle — the value chain

PULT is the seller's marketplace operating system, **not** a loss-finder.

> Finding problems **sells** the product.
> Automation **retains** the product.
> Execution **creates** the value.

Loss-detection is an entry hook and first insight — never the whole product.
After the first insight, the product must open the full operational loop:
**Problem → Cause → Recommendation → Execution → Automation.**

A sprint that stops at "we detected a problem" is **incomplete**.

---

## 2. Layered scope — what the gate applies to

The gate is evaluated against the **end-to-end product chain**, never against
each internal module in isolation:

```
Review Layer (L0–L1)  →  Action Layer (L2–L4)  →  Automation Layer (L3–L4)
   descriptive-only         recommend/execute        system executes
```

**Descriptive-only layers are constitutionally exempt.** The Operational Review
Layer (and any layer whose authority flags are all `False`) is fixed at **L0–L1**
by its own constitution and is **not** required to reach L3–L4. Its L1 output
(`ReviewFinding`) is the **input** to the Action Layer, where L2→L4 happens.

The review boundary stays inviolate: execution lives in the action engine, never
in the review layer. The gate therefore asks whether the *product as a whole*
reaches L3–L4 — not whether any single module does.

> See `operational_review_constitution.md` — `EXECUTION_AUTHORITY=False`,
> `RECOMMENDATION_AUTHORITY=False`, `RANKING_AUTHORITY=False`,
> `DESCRIPTIVE_ONLY=True`. This gate does **not** weaken those flags and requires
> **no** governance unlock of Sprint 76.

---

## 3. Action Maturity Ladder (L0–L4)

| Level | Name | Meaning |
|---|---|---|
| **L0** | Dashboard | We show data. |
| **L1** | Analytics | We show the problem. |
| **L2** | Recommendation | We propose a solution. |
| **L3** | Action | We let the user execute the solution. |
| **L4** | Automation | The system executes the solution itself. |

**Target level for PULT: L3–L4.**

If a sprint's product-level result stays at L0–L2, it must separately justify why
execution or automation is impossible at this stage. Absent that justification,
the sprint is incomplete.

L0–L1 work that lives **inside** a descriptive-only layer is not a violation —
it is that layer's constitutional ceiling (§2).

---

## 4. Acceptance Gate — definition of done

Every sprint must answer: **"What changes in the seller's actions after this
ships?"**

Not sufficient: show a problem · show a number · show a chart · show a
recommendation.

A sprint is complete only if the user gets one of:

1. Action executed automatically.
2. Action in one click.
3. A prepared action that only needs confirmation.
4. A material reduction in manual operational work.

---

## 5. Scope Check — mandatory per sprint

1. What problem do we detect?
2. What solution do we propose?
3. How does the user execute the action?
4. Can the action be executed via API?
5. Can the action be fully automated?
6. How many manual operations are removed for the seller?

If the answers end after Q1 ("we detect a problem"), the sprint is **incomplete**.

---

## 6. Enabler Sprint

An **Enabler Sprint** is infrastructure/platform work that does not directly
create an L3–L4 user action but is a required foundation for future automation.
Examples: new marketplace API integration, telemetry/analytics events, DB
migrations, payment infrastructure, task queues, the automation engine,
permission system, AI infrastructure.

An Enabler is **not** an exemption from product logic. Each Enabler Sprint must
state:

1. Which future user capability it unlocks.
2. Which concrete L3/L4 scenario becomes possible.
3. Through which **Sprint ID** that scenario will be implemented.
4. What happens if the Enabler is not done.

**Verification formula:** `Enabler → Feature → Action → Automation`.
If the chain cannot be built, the sprint is **architectural debt** and requires
separate justification.

**Forbidden:**
- Enabler as a label for technical work with no product outcome.
- Infrastructure with no link to future automation.
- Platform "for the future" with no concrete usage scenario.

Enabler is allowed **only** as an intermediate step toward L3/L4.

> DB-touching Enablers inherit `schema_governance.md`: no model change without a
> matching Alembic migration. Enablers that modify a frozen `backend/logic/` path
> must go through the explicit `CHAR_UPDATE=1` regeneration + diff review — see
> `sprint_71_logic_freeze.md` / `sprint_72_branch_freeze.md`.

---

## 7. Activation Tracking

An Enabler is marked **Done** when its work is complete, but additionally carries
an **Activation Status**: `Pending` / `Activated` / `Expired`.

- **Pending** — Enabler done, the linked L3/L4 scenario not yet shipped.
- **Activated** — the linked L3/L4 scenario has shipped; chain fulfilled.
- **Expired** — the target was reached without activation → the Enabler
  automatically becomes architectural debt.

**Expiry is measured by sprint-sequence ordinal / Sprint ID, never wall-clock.**
Expiry triggers when the declared target Sprint ID is reached without the linked
L3/L4 scenario being activated. This aligns with the no-clock determinism
discipline and creates no conflict with the frozen constitutions.

> Rationale for the no-clock choice: `operational_review_constitution.md` —
> "when" is a deterministic sequence ordinal, never a timestamp.

**Activation Tracking is a governance mechanism, not a product runtime
mechanism.** By default the status lives in governance documents and sprint
documentation. If it is ever stored in the database, the requirements of
`schema_governance.md` and a mandatory Alembic migration automatically apply.

---

## 8. Reference map (no duplication)

This constitution defers to, and does not restate, the following artifacts:

| Reference | Used for |
|---|---|
| `operational_review_constitution.md` | L0–L1 descriptive-only boundary; no-clock ordinal discipline |
| `schema_governance.md` | DB persistence of Activation status → migration requirement |
| `sprint_71_logic_freeze.md` | frozen logic paths; Enabler change protocol |
| `sprint_72_branch_freeze.md` | frozen severe-state branches; Enabler change protocol |
| `runtime_application_constitution.md` | the real substrate the Action Layer operates over |
| `operator_console_constitution.md` | operator-facing surface where L2–L4 actions appear |

---

## 9. The governing question

For every new screen, feature, sprint, and scenario, ask:

> "Am I strengthening the seller's operating system, or turning PULT into yet
> another analytics dashboard / loss-finder?"

If the latter — the decision is **wrong** and must be reconsidered.
