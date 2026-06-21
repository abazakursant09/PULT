"""
Daily Decision Feed services (A2+).

The Daily Decision Feed is the single "what needs my decision today" surface over
all six contours. A2 ships only the data foundation: the per-user attention-state
table (decision_feed_state) and the FEED_SOURCES registry — the contract that says,
per contour, where its items come from and how their canonical item_key is formed.
It never stores or duplicates a signal. No aggregation, no feed builder, no
ranking, no priority computation, no API, no UI, no AI.
"""
