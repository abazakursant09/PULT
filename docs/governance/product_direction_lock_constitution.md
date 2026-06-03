# Product Direction Lock Constitution

**Status: ACTIVE GOVERNANCE — MANDATORY.** Before proposing any redesign, new
screen, new sprint, function removal, UX simplification, or architecture change,
this document is the product constitution and must be checked first.

This is the **product-layer** lock. It defers to
`sprint_acceptance_gate_constitution.md` for the sprint mechanics (Action
Maturity Ladder, Acceptance Gate, Enabler, Activation Tracking) — it does not
restate them. See §9 reference map.

---

## 1. What PULT is

PULT is the **seller's operating system** — the command center for a marketplace
business.

PULT is **NOT**: a dashboard · an analytics panel · a loss-finder · a reporting
system · a recommendation tool.

Problem-finding is only the **entry** into the value system. Value is created
when PULT helps **execute** an action or does it **automatically**.

---

## 2. The product chain

```
Problem → Cause → Solution → Execution → Automation → Result
```

A scenario that stops at `Problem → Cause → Solution` is **incomplete**.
Every function must push toward `Execution → Automation → Result`.

---

## 3. Product priorities

On any conflict, this order always holds:

```
Automation > Execution > Recommendation > Analytics
```

- A change that improves analytics but **reduces automation** → **wrong**.
- A change that simplifies the interface but **removes useful automation** →
  **wrong**.

---

## 4. The five loops (all preserved simultaneously)

1. **Money** — losses of profit, margin, penalties, inefficiency.
2. **Solutions** — action recommendations.
3. **Automation** — automatic action execution via integrations and API.
4. **Reputation** — reviews, auto-replies, negative handling, rating.
5. **Growth** — ads, promotion, SEO, product cards, sales scaling.

**Removing any loop is forbidden.**

---

## 5. Functions that must not be cut or degraded

Without a separate written business justification, it is forbidden to remove or
degrade:

API integrations · action automation · review auto-replies · review management ·
ad tools · growth tools · SEO tools · bulk/mass operations · one-click execution ·
automatic scenarios · retention scenarios · growth scenarios.

> **Interface may be simplified. Functionality may not.**

---

## 6. Check before removing any function

Mandatory analysis before proposing removal:

Daily Usage · Weekly Usage · Retention Impact · Habit Formation · Revenue Impact ·
LTV Impact.

If **even one** axis is positive, removal is **forbidden** pending separate
justification.

A redesign must not worsen any of these axes without explicit justification.

---

## 7. Check before any product proposal

Answer:

1. Does it strengthen the seller's operating system?
2. Does it help execute actions?
3. Does it increase automation?
4. Does it move toward **L3–L4** on the Action Maturity Ladder?
5. Will the user do less manual work?
6. Does the user get a result without extra steps?

If **any** answer is "no", the decision needs reconsideration.

---

## 8. Special rule

"Finding a problem" is **not** value. Value exists only when, after detection,
the user can fix it **in one action** or PULT fixes it **automatically**.

> Loss-finding **sells** the product.
> Automation **retains** the user.
> Execution **creates** the value.

Any proposal that turns PULT into an analytics dashboard is an error and must be
rejected.

---

## 9. Reference map (no duplication)

| Reference | Used for |
|---|---|
| `sprint_acceptance_gate_constitution.md` | Action Maturity Ladder (L0–L4), Acceptance Gate, Scope Check, Enabler, Activation Tracking — the sprint-level enforcement of §2, §3, §7 |
| `operational_review_constitution.md` | the descriptive-only L0–L1 layer; its boundary is intentionally exempt from §7's L3–L4 target |
| `operator_console_constitution.md` | operator surface where L2–L4 actions appear |
| `runtime_application_constitution.md` | the substrate the action/automation loops operate over |

---

## 10. The governing question

Before every product decision:

> "Am I strengthening the seller's operating system, or turning PULT into an
> analytics dashboard?"

If the latter — the decision is **wrong** and must be reconsidered.
