"""PULT-LAUNCH-2.3 — DB-level guarantees for loss-promotion protection (feature OFF).

Every SAFETY invariant the design says the DATABASE must enforce is proven here against
SQLite with foreign keys ON — the same enforcement PostgreSQL gives in production:
a product policy can only point at a REAL placement (no foreign/unplaced product); one
store-wide policy per store; one product policy per (store, product); one ACTIVE action
per (store, product, promo) even when promo_id is NULL; append-only evidence keeps NULL
as NULL; Decimal money round-trips without float. Plus migration up/down/re-upgrade,
single head, and proof the feature ships with NO runtime path.
"""
import os
import sqlite3
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError

import models  # noqa: F401 — register full metadata
from database import Base
from models.protection import ProtectionPolicy, PROMO_KEY_SENTINEL


def _fk_engine():
    eng = create_engine("sqlite://")

    @event.listens_for(eng, "connect")
    def _fk(dbapi_con, _rec):          # SQLite enforces FKs only when asked
        dbapi_con.execute("PRAGMA foreign_keys=ON")

    return eng


@pytest.fixture
def conn():
    eng = _fk_engine()
    c = eng.connect().execution_options(isolation_level="AUTOCOMMIT")
    Base.metadata.create_all(c)
    c.execute(text("INSERT INTO users(id,email,name,hashed_password) VALUES('u1','a@b.c','A','x')"))
    c.execute(text("INSERT INTO workspaces(id,owner_user_id,created_at) VALUES('ws1','u1',CURRENT_TIMESTAMP)"))
    for acc, mp in (("accW", "wildberries"), ("accO", "ozon")):
        c.execute(text("INSERT INTO marketplace_accounts(id,workspace_id,marketplace,identity_status) "
                       "VALUES(:a,'ws1',:m,'unverified')"), {"a": acc, "m": mp})
    _store(c, "s1", "accW", "wildberries", "primary")
    _store(c, "s2", "accO", "ozon", "primary")
    _product(c, "p1", "accW")
    _product(c, "p2", "accW")          # never placed
    _product(c, "pO", "accO")
    _placement(c, "pp1", "p1", "s1", "accW")   # p1 IS placed in s1
    _placement(c, "ppO", "pO", "s2", "accO")   # pO placed in the OTHER store
    yield c
    c.close()


def _store(c, sid, acc, mp, key):
    c.execute(text(
        "INSERT INTO marketplace_stores"
        "(id,marketplace_account_id,marketplace,store_key,label,source,status,created_at,updated_at) "
        "VALUES(:id,:a,:m,:k,'S','manual','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),
        {"id": sid, "a": acc, "m": mp, "k": key})


def _product(c, pid, acc, mp="wildberries", sku="SKU"):
    c.execute(text(
        "INSERT INTO products(id,user_id,name,marketplace,sku,marketplace_account_id) "
        "VALUES(:id,'u1','N',:mp,:sku,:acc)"), {"id": pid, "mp": mp, "sku": sku, "acc": acc})


def _placement(c, ppid, pid, sid, acc):
    c.execute(text(
        "INSERT INTO product_placements"
        "(id,product_id,marketplace_store_id,marketplace_account_id,status,source,first_seen_at,last_seen_at) "
        "VALUES(:id,:p,:s,:a,'active','csv',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),
        {"id": ppid, "p": pid, "s": sid, "a": acc})


def _policy(c, pid, store, product=None, **over):
    cols = dict(id=pid, marketplace_store_id=store, product_id=product)
    c.execute(text(
        "INSERT INTO protection_policies(id,marketplace_store_id,product_id) "
        "VALUES(:id,:marketplace_store_id,:product_id)"), cols)
    for k, v in over.items():
        c.execute(text(f"UPDATE protection_policies SET {k}=:v WHERE id=:id"), {"v": v, "id": pid})


def _action(c, aid, store, product, *, policy="pol1", promo=None, promo_key=None,
            state="detected", idem=None):
    if promo_key is None:
        promo_key = promo if promo is not None else PROMO_KEY_SENTINEL
    c.execute(text(
        "INSERT INTO protection_action_states"
        "(id,policy_id,marketplace_store_id,product_id,promo_id,promo_key,state,idempotency_key) "
        "VALUES(:id,:pol,:s,:p,:promo,:pk,:st,:idem)"),
        {"id": aid, "pol": policy, "s": store, "p": product, "promo": promo, "pk": promo_key,
         "st": state, "idem": idem or aid})


# ── 1. tables + columns exist ────────────────────────────────────────────────
def test_tables_and_columns(conn):
    for t in ("protection_policies", "protection_tax_settings", "protection_additional_costs",
              "protection_evaluations", "protection_action_states"):
        assert t in Base.metadata.tables
    cols = {c[1] for c in conn.execute(text("PRAGMA table_info('protection_policies')"))}
    for c in ("emergency_abs", "emergency_pct", "target_margin_pct", "include_ad_spend",
              "consent_at", "consent_version", "consent_revoked_at", "enabled"):
        assert c in cols


# ── 2/3. defaults ────────────────────────────────────────────────────────────
def test_policy_defaults(conn):
    _policy(conn, "polD", "s1")   # store-wide
    row = conn.execute(text("SELECT enabled,emergency_abs,target_margin_pct,include_ad_spend "
                            "FROM protection_policies WHERE id='polD'")).fetchone()
    assert row[0] == 0                     # enabled default false
    assert Decimal(str(row[1])) == Decimal("0")
    assert Decimal(str(row[2])) == Decimal("10")
    assert row[3] == 0                     # 2.4: include_ad_spend default false (opt-in)


# ── 4. one store-wide policy per store ───────────────────────────────────────
def test_one_store_wide_per_store(conn):
    _policy(conn, "polA", "s1")            # product_id NULL
    with pytest.raises(IntegrityError):
        _policy(conn, "polB", "s1")        # second store-wide → blocked


# ── 5. one product policy per (store, product) ───────────────────────────────
def test_one_product_policy_per_pair(conn):
    _policy(conn, "polA", "s1", "p1")
    with pytest.raises(IntegrityError):
        _policy(conn, "polB", "s1", "p1")


# ── 6. product of another store/account is impossible (composite FK) ─────────
def test_foreign_product_blocked(conn):
    with pytest.raises(IntegrityError):
        _policy(conn, "polX", "s1", "pO")   # pO is placed in s2/accO, not s1


# ── 7. product without a placement is impossible ─────────────────────────────
def test_unplaced_product_blocked(conn):
    with pytest.raises(IntegrityError):
        _policy(conn, "polX", "s1", "p2")   # p2 exists but is not placed in s1


# ── 8. store-wide and product policies coexist ───────────────────────────────
def test_store_and_product_coexist(conn):
    _policy(conn, "polS", "s1")             # store-wide
    _policy(conn, "polP", "s1", "p1")       # product
    n = conn.execute(text("SELECT count(*) FROM protection_policies WHERE marketplace_store_id='s1'")).scalar()
    assert n == 2


# ── 9. tax is one-to-one ─────────────────────────────────────────────────────
def _tax(c, tid, policy, **over):
    base = dict(id=tid, policy_id=policy, tax_mode="none", applied_to="contribution")
    base.update(over)
    keys = ",".join(base)
    vals = ",".join(f":{k}" for k in base)
    c.execute(text(f"INSERT INTO protection_tax_settings({keys}) VALUES({vals})"), base)


def test_tax_one_to_one(conn):
    _policy(conn, "pol1", "s1")
    _tax(conn, "t1", "pol1")
    with pytest.raises(IntegrityError):
        _tax(conn, "t2", "pol1")            # second tax for the same policy → blocked


# ── 10. tax mode CHECK matrix ────────────────────────────────────────────────
def test_tax_mode_matrix(conn):
    _policy(conn, "pol1", "s1")
    # percent requires tax_rate, forbids tax_per_unit
    with pytest.raises(IntegrityError):
        _tax(conn, "tp", "pol1", tax_mode="percent")                 # no tax_rate
    with pytest.raises(IntegrityError):
        _tax(conn, "tu", "pol1", tax_mode="per_unit")                # no tax_per_unit
    with pytest.raises(IntegrityError):
        _tax(conn, "tn", "pol1", tax_mode="none", tax_rate="5")      # none must be empty
    _tax(conn, "tok", "pol1", tax_mode="percent", tax_rate="7")      # valid
    with pytest.raises(IntegrityError):
        _tax(conn, "tbad", "pol1", tax_mode="bogus")                 # bad mode


# ── 11. additional cost CHECK matrix ─────────────────────────────────────────
def _cost(c, cid, policy, **over):
    base = dict(id=cid, policy_id=policy, name="fee", amount="5",
                calculation_type="per_unit", currency="RUB")
    base.update(over)
    keys = ",".join(base); vals = ",".join(f":{k}" for k in base)
    c.execute(text(f"INSERT INTO protection_additional_costs({keys}) VALUES({vals})"), base)


def test_additional_cost_checks(conn):
    _policy(conn, "pol1", "s1")
    with pytest.raises(IntegrityError):
        _cost(conn, "c1", "pol1", amount="-1")                        # negative
    with pytest.raises(IntegrityError):
        _cost(conn, "c2", "pol1", name="")                           # empty name
    with pytest.raises(IntegrityError):
        _cost(conn, "c3", "pol1", currency="rub")                    # not normalized
    with pytest.raises(IntegrityError):
        _cost(conn, "c4", "pol1", calculation_type="percent_of_revenue", amount="150")  # >100
    with pytest.raises(IntegrityError):
        _cost(conn, "c5", "pol1", calculation_type="bogus")          # bad type
    _cost(conn, "cok", "pol1", calculation_type="percent_of_revenue", amount="12.5")    # valid


# ── 12. Decimal money round-trips (no float) ─────────────────────────────────
def test_decimal_round_trip(conn):
    t = ProtectionPolicy.__table__
    conn.execute(t.insert().values(id="polD", marketplace_store_id="s1", product_id=None,
                                   emergency_abs=Decimal("12.34"), target_margin_pct=Decimal("7.500")))
    row = conn.execute(sa.select(t.c.emergency_abs, t.c.target_margin_pct)
                       .where(t.c.id == "polD")).one()
    assert row[0] == Decimal("12.34") and isinstance(row[0], Decimal)
    assert row[1] == Decimal("7.500")


# ── 13. evaluation keeps NULL as NULL (never 0) ──────────────────────────────
def test_evaluation_null_not_zero(conn):
    _policy(conn, "pol1", "s1")
    conn.execute(text(
        "INSERT INTO protection_evaluations"
        "(id,policy_id,marketplace_store_id,product_id,projected_contribution,contribution_pct,"
        " verdict,missing_fields,reasons,inputs_snapshot,evaluated_at) "
        "VALUES('e1','pol1','s1',NULL,NULL,NULL,'incomplete','[]','[]','{}',CURRENT_TIMESTAMP)"))
    row = conn.execute(text("SELECT projected_contribution,contribution_pct FROM protection_evaluations "
                            "WHERE id='e1'")).fetchone()
    assert row[0] is None and row[1] is None          # NULL, not 0


# ── 14. evaluation verdict CHECK ─────────────────────────────────────────────
def test_evaluation_verdict_check(conn):
    _policy(conn, "pol1", "s1")
    with pytest.raises(IntegrityError):
        conn.execute(text(
            "INSERT INTO protection_evaluations"
            "(id,policy_id,marketplace_store_id,verdict,missing_fields,reasons,inputs_snapshot,evaluated_at) "
            "VALUES('e1','pol1','s1','bogus','[]','[]','{}',CURRENT_TIMESTAMP)"))


# ── 2.5A: economic_verdict + actionability are stored in their OWN validated columns ─────────
def _eval(c, eid, *, verdict="complete", econ=None, act=None, snapshot="{}", policy="pol1", store="s1"):
    c.execute(text(
        "INSERT INTO protection_evaluations"
        "(id,policy_id,marketplace_store_id,verdict,economic_verdict,actionability,"
        " missing_fields,reasons,inputs_snapshot,evaluated_at) "
        "VALUES(:id,:pol,:s,:v,:e,:a,'[]','[]',:snap,CURRENT_TIMESTAMP)"),
        {"id": eid, "pol": policy, "s": store, "v": verdict, "e": econ, "a": act, "snap": snapshot})


def test_eval_all_economic_verdicts_allowed(conn):
    _policy(conn, "pol1", "s1")
    for i, ev in enumerate(("safe", "below_target_margin", "emergency_zero_or_loss")):
        _eval(conn, f"e{i}", econ=ev, act="manual_only")
    assert conn.execute(text("SELECT count(*) FROM protection_evaluations")).scalar() == 3


def test_eval_all_actionabilities_allowed(conn):
    _policy(conn, "pol1", "s1")
    for i, a in enumerate(("executable", "manual_only", "unsupported")):
        _eval(conn, f"e{i}", econ="safe", act=a)
    assert conn.execute(text("SELECT count(*) FROM protection_evaluations")).scalar() == 3


def test_eval_null_null_allowed_for_old_row(conn):
    _policy(conn, "pol1", "s1")
    _eval(conn, "e1", econ=None, act=None)         # a pre-2.5A row keeps NULL/NULL
    row = conn.execute(text("SELECT economic_verdict,actionability FROM protection_evaluations "
                            "WHERE id='e1'")).fetchone()
    assert row[0] is None and row[1] is None


def test_eval_bad_economic_verdict_rejected(conn):
    _policy(conn, "pol1", "s1")
    with pytest.raises(IntegrityError):
        _eval(conn, "e1", econ="bogus", act="manual_only")


def test_eval_bad_actionability_rejected(conn):
    _policy(conn, "pol1", "s1")
    with pytest.raises(IntegrityError):
        _eval(conn, "e1", econ="safe", act="bogus")


def test_eval_results_are_independent(conn):
    # calculation_status (verdict) stays separate; the DB enforces NO cross-result rule
    _policy(conn, "pol1", "s1")
    _eval(conn, "e1", verdict="complete", econ="emergency_zero_or_loss", act="unsupported")  # proven loss, no action
    _eval(conn, "e2", verdict="incomplete", econ=None, act="manual_only")                     # incomplete → no economic
    r = conn.execute(text("SELECT verdict,economic_verdict,actionability FROM protection_evaluations "
                          "WHERE id='e1'")).fetchone()
    assert r == ("complete", "emergency_zero_or_loss", "unsupported")


def _eval_run(c, eid, *, run, product, policy="pol1", store="s1"):
    c.execute(text(
        "INSERT INTO protection_evaluations"
        "(id,policy_id,marketplace_store_id,product_id,evaluation_run_id,verdict,"
        " missing_fields,reasons,inputs_snapshot,evaluated_at) "
        "VALUES(:id,:pol,:s,:p,:run,'incomplete','[]','[]','{}',CURRENT_TIMESTAMP)"),
        {"id": eid, "pol": policy, "s": store, "p": product, "run": run})


def test_evaluation_run_id_partial_unique(conn):
    # 2.5B: one Evaluation per (policy, product, run); NULL run_id exempt (history)
    _policy(conn, "pol1", "s1")
    _eval_run(conn, "e1", run="rA", product="X")
    with pytest.raises(IntegrityError):
        _eval_run(conn, "e2", run="rA", product="X")        # duplicate run+product → blocked
    _eval_run(conn, "e3", run="rA", product="Y")            # same run, different product (store-wide) OK
    _eval_run(conn, "e4", run="rB", product="X")            # different run, same product (append-only) OK
    _eval_run(conn, "e5", run=None, product="X")
    _eval_run(conn, "e6", run=None, product="X")            # two NULL run_id rows OK (exempt)
    assert conn.execute(text("SELECT count(*) FROM protection_evaluations")).scalar() == 5


def test_eval_snapshot_preserved(conn):
    _policy(conn, "pol1", "s1")
    _eval(conn, "e1", econ="safe", act="manual_only", snapshot='{"formula_version": "contribution-A-1"}')
    snap = conn.execute(text("SELECT inputs_snapshot FROM protection_evaluations WHERE id='e1'")).scalar()
    assert "contribution-A-1" in snap             # inputs_snapshot untouched, still the evidence


# ── 15/16/17. action uniqueness incl. NULL promo, terminal history ───────────
def test_idempotency_unique(conn):
    _policy(conn, "pol1", "s1")
    _action(conn, "a1", "s1", "p1", promo="PR1", idem="same")
    with pytest.raises(IntegrityError):
        _action(conn, "a2", "s1", "p1", promo="PR2", idem="same")   # duplicate idempotency_key


def test_null_promo_does_not_bypass_active_unique(conn):
    _policy(conn, "pol1", "s1")
    _action(conn, "a1", "s1", "p1", promo=None, idem="k1")          # promo_key = sentinel
    with pytest.raises(IntegrityError):
        _action(conn, "a2", "s1", "p1", promo=None, idem="k2")      # second active same sentinel → blocked


def test_second_active_action_blocked(conn):
    _policy(conn, "pol1", "s1")
    _action(conn, "a1", "s1", "p1", promo="PR1", state="marketplace_processing", idem="k1")
    with pytest.raises(IntegrityError):
        _action(conn, "a2", "s1", "p1", promo="PR1", state="detected", idem="k2")


def test_terminal_history_allows_new_active(conn):
    _policy(conn, "pol1", "s1")
    _action(conn, "a1", "s1", "p1", promo="PR1", state="verified_success", idem="k1")  # terminal
    _action(conn, "a2", "s1", "p1", promo="PR1", state="rejected", idem="k2")          # terminal
    _action(conn, "a3", "s1", "p1", promo="PR1", state="detected", idem="k3")          # new active OK
    n = conn.execute(text("SELECT count(*) FROM protection_action_states")).scalar()
    assert n == 3


# ── cooldown blocks a second command; monitoring continues via Evaluation ────
def test_cooldown_blocks_second_active(conn):
    _policy(conn, "pol1", "s1")
    _action(conn, "a1", "s1", "p1", promo="PR1", state="cooldown", idem="k1")          # cooling down
    with pytest.raises(IntegrityError):
        _action(conn, "a2", "s1", "p1", promo="PR1", state="detected", idem="k2")      # blocked


def test_cooldown_allows_new_evaluation(conn):
    _policy(conn, "pol1", "s1")
    _action(conn, "a1", "s1", "p1", promo="PR1", state="cooldown", idem="k1")
    # monitoring during cooldown = an append-only evaluation, NOT a second action row
    conn.execute(text(
        "INSERT INTO protection_evaluations"
        "(id,policy_id,marketplace_store_id,product_id,verdict,missing_fields,reasons,"
        " inputs_snapshot,evaluated_at) "
        "VALUES('e1','pol1','s1','p1','complete','[]','[]','{}',CURRENT_TIMESTAMP)"))
    assert conn.execute(text("SELECT count(*) FROM protection_evaluations")).scalar() == 1


# ── action scope: store, cabinet and placement are DB-enforced ───────────────
def test_action_policy_store_mismatch_blocked(conn):
    _policy(conn, "pol1", "s1")            # policy lives on s1
    with pytest.raises(IntegrityError):    # action claims store s2 → (pol1,s2) not a policy key
        _action(conn, "a1", "s2", "pO", policy="pol1", promo="PR1", idem="k1")


def test_action_foreign_store_product_blocked(conn):
    _policy(conn, "pol1", "s1")
    with pytest.raises(IntegrityError):    # pO is placed in s2, not s1 → (s1,pO) not a placement
        _action(conn, "a1", "s1", "pO", policy="pol1", promo="PR1", idem="k1")


def test_action_unplaced_product_blocked(conn):
    _policy(conn, "pol1", "s1")
    with pytest.raises(IntegrityError):    # p2 exists but is not placed in s1
        _action(conn, "a1", "s1", "p2", policy="pol1", promo="PR1", idem="k1")


def test_action_valid_for_placed_product(conn):
    # a store-wide policy on s1 may spawn an action ONLY for a product really placed in s1
    _policy(conn, "polWide", "s1")
    _action(conn, "a1", "s1", "p1", policy="polWide", promo="PR1", idem="k1")   # p1 placed in s1 → OK
    # a product-scoped policy's action matches the same store/product
    _policy(conn, "polP", "s1", "p1")
    _action(conn, "a2", "s1", "p1", policy="polP", promo="PR2", idem="k2")      # OK
    assert conn.execute(text("SELECT count(*) FROM protection_action_states")).scalar() == 2


def test_promo_key_check(conn):
    _policy(conn, "pol1", "s1")
    with pytest.raises(IntegrityError):     # promo_id set but promo_key mismatched
        _action(conn, "a1", "s1", "p1", promo="PR1", promo_key="WRONG", idem="k1")
    with pytest.raises(IntegrityError):     # promo_id NULL but promo_key not the sentinel
        _action(conn, "a2", "s1", "p1", promo=None, promo_key="PR1", idem="k2")


# ── 18. execution_log FK uses the real ExecutionLog.id ───────────────────────
def test_execution_log_fk(conn):
    _policy(conn, "pol1", "s1")
    conn.execute(text("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status) "
                      "VALUES('log1','u1','set_price','manual_l3','{}','success')"))
    conn.execute(text(
        "INSERT INTO protection_action_states"
        "(id,policy_id,marketplace_store_id,product_id,promo_key,idempotency_key,execution_log_id) "
        "VALUES('a1','pol1','s1','p1','__none__','k1','log1')"))          # valid FK
    with pytest.raises(IntegrityError):
        conn.execute(text(
            "INSERT INTO protection_action_states"
            "(id,policy_id,marketplace_store_id,product_id,promo_key,idempotency_key,execution_log_id) "
            "VALUES('a2','pol1','s1','p1','__none__','k2','ghost')"))     # dangling FK → blocked


# ── 19. reappear_count CHECK ─────────────────────────────────────────────────
def test_reappear_count_check(conn):
    _policy(conn, "pol1", "s1")
    with pytest.raises(IntegrityError):
        conn.execute(text(
            "INSERT INTO protection_action_states"
            "(id,policy_id,marketplace_store_id,product_id,promo_key,idempotency_key,reappear_count) "
            "VALUES('a1','pol1','s1','p1','__none__','k1',-1)"))
    with pytest.raises(IntegrityError):     # bad state value
        conn.execute(text(
            "INSERT INTO protection_action_states"
            "(id,policy_id,marketplace_store_id,product_id,promo_key,idempotency_key,state) "
            "VALUES('a2','pol1','s1','p1','__none__','k2','bogus')"))


# ── 20. cascade / set-null behaviour ─────────────────────────────────────────
def test_cascade_and_setnull(conn):
    _policy(conn, "pol1", "s1")
    _tax(conn, "t1", "pol1")
    _cost(conn, "c1", "pol1")
    _action(conn, "a1", "s1", "p1", promo="PR1", idem="k1")
    conn.execute(text(
        "INSERT INTO protection_evaluations"
        "(id,policy_id,marketplace_store_id,verdict,missing_fields,reasons,inputs_snapshot,evaluated_at) "
        "VALUES('e1','pol1','s1','complete','[]','[]','{}',CURRENT_TIMESTAMP)"))
    conn.execute(text("DELETE FROM protection_policies WHERE id='pol1'"))
    # tax / cost / action cascade away; the evaluation survives with policy_id set NULL (evidence)
    assert conn.execute(text("SELECT count(*) FROM protection_tax_settings")).scalar() == 0
    assert conn.execute(text("SELECT count(*) FROM protection_additional_costs")).scalar() == 0
    assert conn.execute(text("SELECT count(*) FROM protection_action_states")).scalar() == 0
    ev = conn.execute(text("SELECT policy_id FROM protection_evaluations WHERE id='e1'")).fetchone()
    assert ev is not None and ev[0] is None            # evidence kept, policy_id nulled


def test_store_delete_cascades_policy(conn):
    _policy(conn, "pol1", "s1")
    conn.execute(text("DELETE FROM marketplace_stores WHERE id='s1'"))
    assert conn.execute(text("SELECT count(*) FROM protection_policies WHERE id='pol1'")).scalar() == 0


# ── 21/22. migration up/down/re-upgrade + single head ────────────────────────
def test_migration_upgrade_downgrade_reupgrade(tmp_path):
    from alembic.config import Config
    from alembic import command

    db = tmp_path / "mig.db"
    os.environ["ALEMBIC_DATABASE_URL"] = f"sqlite+aiosqlite:///{db.as_posix()}"
    try:
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "sdp1a2b3c4d01")        # pre-protection revision
        con = sqlite3.connect(db)
        pre = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'protection_%'")]
        con.close()
        assert pre == []                              # no protection tables yet

        command.upgrade(cfg, "head")
        con = sqlite3.connect(db)
        post = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'protection_%'")}
        con.close()
        assert post == {"protection_policies", "protection_tax_settings",
                        "protection_additional_costs", "protection_evaluations",
                        "protection_action_states"}

        command.downgrade(cfg, "sdp1a2b3c4d01")
        con = sqlite3.connect(db)
        gone = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'protection_%'")]
        con.close()
        assert gone == []                             # downgrade removes only new tables

        command.upgrade(cfg, "head")                  # re-upgrade must succeed
    finally:
        os.environ.pop("ALEMBIC_DATABASE_URL", None)


def test_single_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["tkv1a2b3c4d01"], heads


# ── 23/24/25. feature stays OFF; stop_auto_promotion stays contained ─────────
def test_feature_has_no_runtime_path():
    import inspect
    import tasks.scheduler as sched
    src = inspect.getsource(sched)
    assert "protection" not in src.lower()            # no scheduler wiring for protection


def test_stop_auto_promotion_still_contained():
    from services.decision_outcome.decision_bridge import capability_supported
    assert capability_supported("stop_auto_promotion", "wildberries") is False
    assert capability_supported("stop_auto_promotion", "ozon") is False
