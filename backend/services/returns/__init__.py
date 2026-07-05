"""Returns Diagnosis contour (Phase R1b) — read-only diagnosis of a rising return RATE: the
seller's OWN observed returns-per-unit-sold drifting UP over time (ImportedReturnRow.returns_qty
÷ ImportedFinanceRow.quantity units sold). Self-referential — compares this product's recent
return rate to its OWN earlier rate; NEVER an absolute floor, category benchmark, or competitor.

DOUBLE-COUNT DISCIPLINE: the diagnosis is built from return FREQUENCY/RATE only (returns_qty vs
units sold). It NEVER reads net_profit, and NEVER treats return_amount as a profit loss —
net_profit may already reflect return effects, so a money-loss claim would double-count.

Pure diagnosis: no treatment, no executor, no forecast, no marketplace write. DB-headless. Reads
observed uploaded data only (imported_return_rows + imported_finance_rows), never a marketplace
API. Distinct from every other contour; independent module."""
