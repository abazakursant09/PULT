"""HOTFIX — imported link_status model↔migration parity (LAUNCH-1.4.4).

The imp4 migration created a CHECK `link_status IN ('linked','unassigned','conflict')` on the four
Imported* tables, but the ORM models never declared it. Alembic 1.19.0 enabled SQLite CHECK-constraint
comparison in `alembic check`, so `test_schema_drift` began (correctly) reporting four
`remove_constraint` drifts on master after the C3A merge — a pre-existing gap, not a C3A defect. This
locks the declarations so model metadata matches the Alembic head. It is a METADATA parity fix only: the
DB already has these constraints; no new migration, no schema/data change, no runtime/value change.
"""
from database import Base
import models  # noqa: F401 — registers every table in Base.metadata

_EXPECT = {
    "imported_card_content_rows": "ck_imported_card_content_rows_link_status",
    "imported_finance_rows": "ck_imported_finance_rows_link_status",
    "imported_product_rows": "ck_imported_product_rows_link_status",
    "imported_return_rows": "ck_imported_return_rows_link_status",
}
_ALLOWED = ("linked", "unassigned", "conflict")


def _link_status_checks(table):
    return [c for c in table.constraints
            if c.__class__.__name__ == "CheckConstraint" and "link_status" in str(c.sqltext)]


def test_all_four_imported_tables_present():
    for tbl in _EXPECT:
        assert tbl in Base.metadata.tables, f"{tbl} missing from metadata"


def test_exactly_one_link_status_check_with_imp4_name():
    for tbl, name in _EXPECT.items():
        checks = _link_status_checks(Base.metadata.tables[tbl])
        assert len(checks) == 1, f"{tbl}: expected exactly one link_status CHECK, got {len(checks)}"
        # Name must EXACTLY match the imp4 migration name (no double ck_<table>_ck_<table> prefix).
        assert checks[0].name == name, f"{tbl}: constraint name {checks[0].name!r} != {name!r}"


def test_check_allows_only_the_three_values():
    for tbl in _EXPECT:
        sql = str(_link_status_checks(Base.metadata.tables[tbl])[0].sqltext)
        for v in _ALLOWED:
            assert f"'{v}'" in sql, f"{tbl}: {v!r} missing from CHECK"
        # No extra literal value slipped in.
        assert sql.count("'") == 2 * len(_ALLOWED), f"{tbl}: unexpected literals in {sql!r}"


def test_link_status_column_shape_unchanged():
    for tbl in _EXPECT:
        col = Base.metadata.tables[tbl].columns["link_status"]
        assert col.nullable is False
        assert col.default.arg == "unassigned"
        assert col.server_default.arg == "unassigned"
        assert col.type.length == 10


def test_other_constraints_and_indexes_preserved():
    # The parity fix must ADD only the CHECK — every pre-existing index/constraint stays.
    expect_indexes = {
        "imported_card_content_rows": {"ix_imp_card_user_mp", "ix_imp_card_product_id",
                                       "uq_imp_card_api_row"},
        "imported_finance_rows": {"ix_imp_finance_user_mp", "ix_imp_finance_product_id"},
        "imported_product_rows": {"ix_imp_product_user_mp", "ix_imp_product_product_id",
                                  "uq_imp_product_api_row"},
        "imported_return_rows": {"ix_imp_returns_user_mp", "ix_imp_returns_product_id"},
    }
    for tbl, idx in expect_indexes.items():
        have = {i.name for i in Base.metadata.tables[tbl].indexes}
        assert idx <= have, f"{tbl}: lost indexes {idx - have}"


def test_single_alembic_head_unchanged():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["rob1a2b3c4d01"]
