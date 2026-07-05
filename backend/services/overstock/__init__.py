"""Overstock / Dead Stock Diagnosis contour (Phase 7.1) — read-only diagnosis of OVERSTOCK:
products whose on-hand stock is too high relative to the seller's OWN recent observed sales
velocity (money frozen in inventory). The MIRROR of Supply Diagnosis — Supply flags too-little
runway (stock-out), Overstock flags too-much (excess cover) or no movement at all (dead stock).
Pure diagnosis: no treatment, no executor, no forecast, no benchmark, no competitor compare, no
discount/liquidation action — observed present overstock only. DB-headless. DISTINCT from Supply
(does not reuse supply_signal), Revenue, Money Leak, and Pricing."""
