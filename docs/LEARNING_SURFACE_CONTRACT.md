# Learning Surface Contract (E3)

Frontend-facing contract for the Decision Learning surface. It is a **pure
composition of two existing read-only backend endpoints** — no backend change is
required to populate it.

Source endpoints:

- `GET /api/learning/alternatives?insight_key=&listing_id=` → ranked alternatives + `degraded`
- `GET /api/learning/evidence?insight_key=&action_key=` → evidence for one action

Both are authenticated (`get_current_user`), read-only, and report
`source: "decision_memory"`.

---

## 1. LearningSurface model

```jsonc
{
  "insight_key": "margin_crisis:wb:SKU1",

  "recommended_action": {            // null when alternatives is empty
    "action_key": "reduce_discount",
    "rank": 1,
    "reason": "4 of 5 similar cases confirmed profit improvement (recent outcomes weighted)",
    "confidence_source": "decision_memory"
  },

  "evidence": {                      // E2 evidence block; null when no recommendation
    "action_key": "reduce_discount",
    "reason": "...",
    "context_group": "wb|electronics|mid|high_margin",
    "confirmed": 4,
    "refuted": 1,
    "sample": 5,
    "confirmed_rate": 0.8,
    "weighted_rate": 0.8,
    "fallback": false,
    "source": "decision_memory"
  },

  "alternatives": [                  // L6 alternatives block, full list, never hidden
    { "action_key": "reduce_discount", "rank": 1, "reason": "...", "fallback": false,
      "confirmed": 4, "refuted": 1, "sample": 5, "confirmed_rate": 0.8, "weighted_rate": 0.8 },
    { "action_key": "set_price", "rank": 2, "reason": "...", "fallback": false, "...": "..." },
    { "action_key": "stop_auto_promotion", "rank": 3, "reason": "...", "fallback": true, "...": "..." }
  ],

  "degraded": false,
  "source": "decision_memory",
  "ui_state": "ranked"               // derived: "ranked" | "fallback" | "empty"
}
```

### Recommendation block (req 2)

| field | type | source |
|---|---|---|
| `action_key` | string | `alternatives[0].action_key` |
| `rank` | int (always 1) | `alternatives[0].rank` |
| `reason` | string | `alternatives[0].reason` |
| `confidence_source` | `"decision_memory"` | alternatives response `.source` |

`recommended_action` is `null` iff `alternatives` is empty.

### Evidence block (req 3)

Reuses the E2 `Evidence` contract verbatim. Fetched with
`(insight_key, recommended_action.action_key)`. `null` when there is no
recommendation.

### Alternatives block (req 4)

Reuses the L6 `Alternative` contract verbatim — the full ranked list. **Never
hidden**, in any state.

---

## 2. Mapping table (contract ← API)

| Surface field | Comes from | Endpoint |
|---|---|---|
| `insight_key` | `AlternativesResponse.insight_key` | alternatives |
| `recommended_action.action_key` | `alternatives[0].action_key` | alternatives |
| `recommended_action.rank` | `alternatives[0].rank` | alternatives |
| `recommended_action.reason` | `alternatives[0].reason` | alternatives |
| `recommended_action.confidence_source` | `AlternativesResponse.source` | alternatives |
| `evidence` | `EvidenceResponse.evidence` | evidence (called with `recommended_action.action_key`) |
| `alternatives[]` | `AlternativesResponse.alternatives` | alternatives |
| `degraded` | `AlternativesResponse.degraded` | alternatives |
| `source` | `AlternativesResponse.source` | alternatives |
| `ui_state` | derived (see §3) | — |
| fallback banner trigger | `alternatives[0].fallback` (== `evidence.fallback`) | alternatives / evidence |

---

## 3. UI state rules

`ui_state` is derived client-side from the alternatives response:

```
if alternatives == []                 -> "empty"
elif alternatives[0].fallback == true -> "fallback"
else                                  -> "ranked"
```

### Degraded behavior (req 5)

`degraded == true` (resolved `context_group` still has an `unknown` segment):

- **show** the recommendation,
- **show** a degraded-context warning ("Ranked on limited business context."),
- **never hide** the alternatives list.

Degraded is orthogonal to `ui_state` — a `ranked` or `fallback` surface can also
be degraded.

### Empty behavior (req 6)

`ui_state == "empty"` (no alternatives — malformed/unsupported insight):

- `recommended_action` and `evidence` are `null`,
- show: **"Not enough history yet."**

### Fallback behavior (req 7)

`ui_state == "fallback"` (alternatives present but `alternatives[0].fallback == true`
— no/insufficient outcome history under the context):

- still show the recommendation (deterministic default order),
- show: **"Using default action order."**

---

## 4. Examples

### Ranked (history present, enriched context)
```jsonc
{ "ui_state": "ranked", "degraded": false,
  "recommended_action": { "action_key": "reduce_discount", "rank": 1,
    "reason": "4 of 5 similar cases confirmed profit improvement (recent outcomes weighted)",
    "confidence_source": "decision_memory" },
  "evidence": { "fallback": false, "sample": 5, "confirmed_rate": 0.8, "weighted_rate": 0.8, "...": "..." },
  "alternatives": [ /* 3 items */ ] }
```

### Fallback (no history)
```jsonc
{ "ui_state": "fallback", "degraded": false,
  "recommended_action": { "action_key": "set_price", "rank": 1,
    "reason": "Not enough history. Using default action order.",
    "confidence_source": "decision_memory" },
  "evidence": { "fallback": true, "sample": 0, "confirmed_rate": null, "weighted_rate": null, "...": "..." },
  "alternatives": [ /* 3 fallback items */ ] }
// banner: "Using default action order."
```

### Degraded (no domain data, history present in degraded context)
```jsonc
{ "ui_state": "ranked", "degraded": true,
  "recommended_action": { "action_key": "reduce_discount", "rank": 1, "...": "..." },
  "alternatives": [ /* shown, never hidden */ ] }
// banner: degraded-context warning + recommendation + alternatives
```

### Empty (malformed / unsupported insight)
```jsonc
{ "ui_state": "empty", "degraded": true,
  "recommended_action": null, "evidence": null, "alternatives": [] }
// message: "Not enough history yet."
```

---

## 5. Audit — populatability (req 9)

| Requirement | Satisfiable from existing APIs? | Note |
|---|---|---|
| Recommendation block | ✅ | `alternatives[0]` + response `source` |
| Evidence block (shape) | ✅ | E2 endpoint, called with recommended action_key |
| Alternatives block | ✅ | L6 endpoint, full list |
| `confidence_source = "decision_memory"` | ✅ | both endpoints emit `source` |
| degraded | ✅ | `AlternativesResponse.degraded` |
| empty / fallback distinction | ✅ | `[]` vs `alternatives[0].fallback` |
| **Evidence ↔ recommendation context consistency** | ✅ (E4) | when the same `listing_id` is passed to both |

The contract is **populatable and consistent**.

### BLOCKER-1 — RESOLVED in E4

Previously `/alternatives` and `/evidence` resolved `context_group` from
different inputs (listing vs insight_key parsing), so the evidence block could
reflect a more-degraded context than the recommendation.

**Fix (E4, `fix(learning): keep evidence consistent with ranked alternatives`):**
`get_decision_evidence` now resolves context the **same way** `/alternatives`
does — `resolve_context_group_for_insight(insight_key, listing_id)` then
`get_ranked_alternatives` — and `GET /evidence` gained an optional `listing_id`
query param (response shape unchanged). The evidence row is literally the ranked
alternative row, so for the same `(insight_key, action_key, listing_id)` the
`reason / confirmed / refuted / sample / confirmed_rate / weighted_rate /
fallback / context_group` all match the alternatives surface by construction.

Pinned by `test_listing_backed_context_consistent_across_endpoints`,
`test_surface_enriched_consistent`, and
`test_evidence_matches_top_alternative`.

### Frontend guidance

**Pass the same `listing_id` to `/alternatives` and `/evidence`.** Then the
recommendation and its evidence share one context. (The evidence block remains a
strict subset of `alternatives[0]` plus `context_group`; a single-call surface
endpoint that folds both is still a possible future optimization, not required.)
