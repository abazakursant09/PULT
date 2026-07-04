"""Supply / Replenishment Diagnosis contour (Phase 4.1) — read-only diagnosis of stock-out
risk (observed runway = on-hand stock ÷ observed daily sell-through), from the seller's OWN
ImportedProductRow.stock + ImportedFinanceRow.quantity. Pure diagnosis: no treatment, no
executor, no forecast — observed PRESENT runway only. DB-headless. Independent — complements
(does not replace) the operations low_stock signal; does not reuse operations_signal."""
