# Operational Review Constitution (Sprint 76)

The Operational Review Layer adds the missing capability between "operator sees
state" and "operator can review state": **Observe → Review → Record.** Nothing
more. Deterministic, append-only, descriptive-only, operator-mediated,
non-agentic, non-predictive, non-recommendatory. Read-only over the real
substrate (Runtime Application). No existing layer modified.

## Review boundary

`review_boundary.py` — frozen authority flags:
`EXECUTION_AUTHORITY=False`, `MUTATION_AUTHORITY=False`,
`RECOMMENDATION_AUTHORITY=False`, `PREDICTION_AUTHORITY=False`,
`RANKING_AUTHORITY=False`, `DESCRIPTIVE_ONLY=True`, `APPEND_ONLY=True`,
`DETERMINISTIC=True`.

The layer never mutates runtime state, executes actions, recommends actions,
ranks actions, or infers future outcomes.

## Replay boundary

No clocks. **"When reviewed" is a deterministic review-sequence ordinal**
(`ReviewLedgerEntry.review_sequence`), never a wall-clock timestamp. Same
Runtime Application input → same Review Session → same `review_hash`, byte-
identical same-process, cross-process, and on replay reconstruction. Hashing
reuses the frozen Runtime Envelope canonical SHA-256 discipline.

## Immutable structures

| Structure | Role |
|---|---|
| `ReviewFinding` | one descriptive observation (code, subject, catalog text, evidence) |
| `ReviewSnapshot` | findings for one reviewed `runtime_application_hash` + `snapshot_hash` |
| `ReviewLedgerEntry` | append-only record: review_sequence, app hash, snapshot hash, finding count |
| `ReviewLedger` | append-only sequence of entries |
| `OperationalReviewSession` | ordered snapshots + ledger + sealed `review_hash` |

All are frozen dataclasses. The ledger and session are append-only (`record`,
`build_review_session` never alter prior entries).

## Findings — descriptive only

Findings come from a CLOSED catalog (`FINDING_CATALOG`):
- "pressure accumulation observed"
- "dissipation surface observed"
- "drift visible"
- "instability marker observed"
- "intervention surface present"

Each mirrors a structure already present in the topology, in the past tense,
with numeric evidence only. Structurally forbidden (verified against finding
output): "should investigate", "recommend action", "likely failure",
"expected degradation", and any recommendation/prediction/ranking vocabulary.

## Review ledger

Append-only record letting an operator capture: what was observed (findings),
when it was reviewed (deterministic ordinal), and which `runtime_application_hash`
was reviewed. No execution authority, no workflow/task/ticket system, no state
mutation.

## Forbidden behaviors

mutate runtime state · execute actions · recommend actions · rank actions ·
infer future outcomes · workflow/task/ticket engines · clocks · randomness ·
network. None present.

## Immutable contracts (no change without governance unlock)

1. Authority flags (all False except descriptive/append-only/deterministic).
2. `FINDING_CATALOG` closed phrase set.
3. Hash domains: `PULT-REVIEW-FINDING/1`, `PULT-REVIEW-SNAPSHOT/1`,
   `PULT-REVIEW-LEDGER/1`, `PULT-OPERATIONAL-REVIEW/1`.
4. "When reviewed" = review-sequence ordinal (no clock).
5. Append-only ledger/session lineage.
6. The six golden fixture review hashes under `tests/operational_review_fixtures/`.
