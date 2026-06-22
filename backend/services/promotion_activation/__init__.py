"""
Promotion Activation services (A2+).

Turns the (already-built) Decision Outcome promotion path ON: it runs the EXISTING
candidate engine + promotion writer + decision bridge so that eligible actionable
engine signals become Decisions — and therefore a decision_id flows into the feed
and the manual apply UX becomes reachable. It duplicates no logic, triggers no
execution, applies nothing, opens no measurement, calls no marketplace. A Decision
is an intent record; apply stays manual. A2 ships the runner + the append-only
promotion_run ledger. No scheduler, no API, no UI, no auto-loop, no AI.
"""
