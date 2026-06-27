# Learning Key Doctrine

> Normative project-level document. This is **not** a report — it is the binding
> reference for the unit of learning in Learning OS. A PR that changes the Learning
> aggregation key, or that adds a new axis to it, must conform to this doctrine or be
> rejected. See also [`canonical-surface-doctrine.md`](./canonical-surface-doctrine.md).

## 1. Unit of learning

The unit of learning is the **observed lever-effect**: how a given executable action,
once applied, was observed to move a specific metric on a specific marketplace.

Learning OS does **not** learn by the *source* of a signal. It learns by the
**observed effect of the lever**. Two different problem detectors that pull the same
physical lever and measure the same metric on the same marketplace are observing the
**same phenomenon**.

## 2. Canonical Learning key

```
marketplace + action_key + metric_key
```

This is the aggregation key for every observed-outcome bucket
(`LearningAggregate`, `aggregate_learning_observations`). It is **read-model only** —
computed at read time from `EngineEffectObservation` (measured rows) joined to
`EngineSignalDecisionLink`. There is no stored aggregate table; changing the key is a
semantic change, never a data migration.

## 3. Marketplace isolation — hard rule

`marketplace` is canonical (`normalize_marketplace`: wb / ozon / yandex / megamarket).
Different marketplaces are **never** merged. `stop_auto_promotion` on `wb` and on
`ozon` are two separate facts, never a blended one. A null/unknown marketplace is its
own bucket, never folded into a real marketplace.

## 4. metric_key — a mandatory axis

`metric_key` is a **required** axis of the key, not an optional refinement. It
separates effects that are not comparable. The same lever measured against a different
metric is a different learning fact.

**Worked example (the canonical case this doctrine was written for):**

| Detector (contour) | action_key | metric_key | Learning bucket |
|---|---|---|---|
| advertising (indirect: `ad_on_low_stock` / `ad_on_bad_listing` / `ad_on_oos_risk`) | `stop_auto_promotion` | `ad_profit_impact` | `(ozon, stop_auto_promotion, ad_profit_impact)` |
| operations (`auto_promo_margin_drain`) | `stop_auto_promotion` | `net_profit` | `(ozon, stop_auto_promotion, net_profit)` |

Both pull `stop_auto_promotion` on `ozon`, yet they **do not pool**, because
`metric_key` differs. The key already keeps them apart — no contour axis is needed.

## 5. contour / detector / signal source are NOT a learning axis

The contour (advertising, operations, pricing, …) and the originating `signal_key`
are **metadata about who detected the problem**, not facts about the lever's observed
effect. They are deliberately **excluded** from the Learning key. Adding them would
fragment samples by detector, slow convergence past the observed-history threshold,
and retroactively re-bucket already-shipped contours — contradicting §1.

## 6. Pooling on full-key collision is intentional

When two observations share **all three** axes
(`marketplace` + `action_key` + `metric_key`), pooling them into one bucket is the
**correct, intended** behavior. Same marketplace + same physical lever + same observed
metric = one observed phenomenon. This is a feature, not an accident.

## 7. If finer detail is ever needed: use context_group, not contour

The sanctioned refinement axis already exists: `context_group`
(`marketplace | category | price_band | margin_band`, Learning OS v4,
`get_action_learning_summary_for_context`). It refines by the **listing's business
context**, which is a property of the observed subject — not by the detector. Any
future need for finer isolation uses this axis. The detector/contour never enters the
key.

## 8. Descriptive, not predictive

Learning OS reports **observed counts** of effect bands
(improved / worsened / unchanged / not_evaluated). It stores no percentage, no score,
no probability, no ROI, no forecast. A read-time caller may render a ratio, but the
layer guarantees only "this is what was observed N times," never "this will happen."

## 9. Prohibited

- Adding `contour` / `signal_key` / any detector axis to the Learning key **without a
  separate doctrine review** that supersedes this document.
- Merging marketplaces (violates §3).
- Using forecast / AI / LLM ranking / competitor data anywhere in the Learning path.
- Presenting Learning output as a **guarantee of result**. It is observed history,
  not a promise.

## 10. Enforcement

- The invariant in §4 is locked by a guard test:
  `backend/tests/test_learning_key_doctrine.py` — proves advertising
  `stop_auto_promotion` (`ad_profit_impact`) and operations `stop_auto_promotion`
  (`net_profit`) resolve to **distinct** Learning buckets, and that the protecting
  axis is `metric_key` (not contour).
- Reviewers of any Learning-key change must check this document and that test.
