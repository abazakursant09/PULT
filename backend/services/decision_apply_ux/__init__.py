"""
Decision Apply UX services (A2+).

Makes the (already-built) bound-decision execution path VISIBLE to the seller —
"can this decision be applied, what would it do?" — without applying anything. A2
ships only the read-only preview (build_apply_preview) + the append-only
decision_apply_intent ledger. No real apply, no executor write, no marketplace
call, no measurement, no API/UI, no AI.
"""
