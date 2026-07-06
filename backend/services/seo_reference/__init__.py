"""Marketplace category-schema Reference INGESTION (Phase C2c) — read-only, GLOBAL Reference Data.

Per the Reference Data Doctrine, this is an ingestion SIBLING of csv_import — it runs OUTSIDE the
Advisory Runtime and is NOT a producer. It pulls marketplace category schema (tree + attributes)
via a read-only marketplace API and writes GLOBAL, versioned rows into marketplace_category_rows /
marketplace_category_attribute_rows (no user_id).

Disjoint from execution: this uses the API only in the READ / Evidence-Snapshot (Reference) role;
it never writes to a marketplace and is never called at diagnosis time. Reference merges into the
CardSnapshot only later (C2d). MegaMarket has no category-schema API → honest skip."""
