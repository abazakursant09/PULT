"""Rating / Reputation Health Diagnosis contour (Phase 5.1) — read-only diagnosis of
aggregate rating DECLINE (reputation deterioration) from the seller's OWN observed
ImportedProductRow.rating across dated import snapshots. Pure diagnosis: no treatment, no
executor, no forecast — observed present decline only. DB-headless. DISTINCT from the
Review contour (individual review handling); does not reuse review_signal."""
