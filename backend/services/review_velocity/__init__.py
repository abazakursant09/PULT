"""Review Acquisition / Social-Proof Velocity Diagnosis contour (Phase 6.1) — read-only
diagnosis of a STALL in the seller's OWN observed reviews-per-unit-sold rate. reviews_count
is monotonic, so this is NOT "reviews declined": it is "review acquisition slowed compared
with this product's OWN earlier observed rate while sales continued". Self-referential —
never an absolute review-rate floor, never a category benchmark, never a competitor compare.
Pure diagnosis: no treatment, no executor, no forecast — observed only. DB-headless
(ImportedProductRow.reviews_count dated snapshots ÷ ImportedFinanceRow.quantity). DISTINCT
from BOTH the Rating contour (rating-value decline; does not reuse rating_signal) and the
Review contour (individual review workflow; does not reuse review_signal)."""
