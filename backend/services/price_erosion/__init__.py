"""Price Erosion / Discount Creep Diagnosis contour (Phase 8.1) — read-only diagnosis of a
product's OWN realized price drifting DOWN across the seller's OWN dated ImportedProductRow.price
snapshots (margin compression). Self-referential: compares this product's baseline price to its
latest confirmed price — NEVER an absolute price floor, category benchmark, or competitor
compare. Pure diagnosis: no treatment, no executor, no forecast, no price-write action —
observed present erosion only. DB-headless. DISTINCT from the executable Pricing contour (which
ACTS on price) — this diagnoses observed own-price decline; does not reuse pricing_signal. Also
distinct from Money Leak / Revenue / Overstock."""
