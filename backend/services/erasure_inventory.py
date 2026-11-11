"""Read-only erasure preparation, NOT a deletion plan or a public API.

Counts only confirmed owner links. Unclassified/shared data, files, backups and
queues explicitly prevent a claim of complete erasure. No secret values selected.
"""
from sqlalchemy import and_, func, inspect, not_, or_, select

from database import Base
import models  # register the complete runtime metadata, without starting the app
from models.user import User
from models.workspace import Workspace
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from services.account_closure_policy import rollout_status
from services.account_closure import review_erasure_eligibility

SHARED_REFERENCE_TABLES = frozenset({
    "marketplace_category_rows", "marketplace_category_attribute_rows",
})
# Reviewed single ownership anchors, not generic FK traversal. Extra foreign keys
# (e.g. observations on decision_outcomes) are NOT authority to erase those parents.
INDIRECT_OWNER_LINKS = (
    ("competitor_analysis", "product_id", "products"),
    ("review_responses", "product_id", "products"),
    ("pricing_rules", "product_id", "products"),
    ("price_change_log", "product_id", "products"),
    ("financial_snapshots", "product_id", "products"),
    ("legal_cases", "product_id", "products"),
    ("execution_recovery_audit", "execution_log_id", "execution_logs"),
    ("decision_outcomes", "decision_id", "decisions"),
)

OWNERLESS_REVIEW = {
    "auth_rate_limit_buckets": "HMAC identity/IP keys with no direct owner FK; not assumed anonymous; TTL/security review required.",
    "monitor_events": "Free-text events without an owner key; content and provenance review required.",
    "promo_codes": "May contain third-party blogger names; not owned by one activating customer.",
    "transport_companies": "Shared counterparty directory may contain personal contacts; do not erase wholesale.",
}


async def dry_run_account_erasure(db, user_id: str) -> dict:
    """Operator-only review report, never a deletion program or authorization.

    Reuse the live inventory rather than trusting caller-supplied row counts.
    Even an empty/conflict-free account cannot pass while external stages,
    retention decisions and a safe purge executor are unimplemented.
    Caller is responsible for authorization, as with inventory_account.
    """
    inventory = await inventory_account(db, user_id)
    cross_party = {item["table"] for item in inventory["cross_party_review"]}
    conflicts = {item["table"]: item["count"]
                 for item in inventory["conflicting_owner_paths"] if item["count"]}
    rows = []
    for item in inventory["counts"]:
        reasons = ["retention_policy_required"]
        if item["table"] in cross_party:
            reasons.append("cross_party_review_required")
        if item["table"] in conflicts:
            reasons.append("conflicting_owner_paths")
        rows.append({"table": item["table"], "observed_owner_rows": item["count"],
                     "ownership_basis": item["basis"], "disposition": "review_only",
                     "reasons": reasons})
    return {
        "format_version": 1,
        "mode": "dry_run",
        "schema_scope": inventory["schema_scope"],
        "status": "blocked",
        "read_only": True,
        "executable": False,
        "complete_erasure_ready": False,
        "ordinary_closure_policy": rollout_status(),
        "account_closure_review": inventory["account_closure_review"],
        "blockers": ["purge_executor_not_implemented", "retention_policy_required",
                     "external_stages_not_verified"]
                    + ([] if inventory["account_closure_review"]["erasure_review_due"]
                       else ["ordinary_closure_not_review_due"])
                    + (["unclassified_tables"] if inventory["unclassified_tables"] else [])
                    + (["conflicting_owner_paths"] if conflicts else []),
        "table_reviews": rows,
        "excluded_shared_tables": inventory["shared_reference_tables"],
        "unclassified_tables": inventory["unclassified_tables"],
        "ownerless_review": inventory["ownerless_review"],
        "conflicting_owner_paths": [{"table": name, "count": count}
                                    for name, count in sorted(conflicts.items())],
        "unverified_stages": inventory["not_inspected"],
        "warning": ("Review only: counts and lifecycle snapshots are not deletion targets or authorization. "
                    "No erasure actions are scheduled."),
    }


async def inventory_account(db, user_id: str) -> dict:
    """Operator-side count report. No user IDs or content returned in the report.

    Callers must supply an authorized owner ID. Deliberately not HTTP-exposed.
    A known count does not authorize destruction or resolve retention obligations.
    """
    with db.no_autoflush:
        user = (await db.execute(select(
            User.id, User.deleted_at, User.closure_policy_version,
            User.closure_started_at, User.closure_purge_started_at,
        ).where(User.id == user_id))).one_or_none()
    if user is None:
        raise ValueError("Account not found")
    closure_review = review_erasure_eligibility(user)

    workspace_ids = select(Workspace.id).where(Workspace.owner_user_id == user_id)
    account_ids = select(MarketplaceAccount.id).where(
        MarketplaceAccount.workspace_id.in_(workspace_ids))
    connection_ids = select(MarketplaceConnection.id).where(
        MarketplaceConnection.user_id == user_id)
    tables = Base.metadata.tables
    product_ids = select(tables["products"].c.id).where(tables["products"].c.user_id == user_id)
    credential_ids = select(ApiCredential.id).where(ApiCredential.connection_id.in_(connection_ids))
    execution_ids = select(tables["execution_logs"].c.id).where(tables["execution_logs"].c.user_id == user_id)
    explicit = {
        User.__table__.name: (User.__table__.c.id == user_id, "account"),
        Workspace.__table__.name: (Workspace.__table__.c.owner_user_id == user_id, "workspace_owner"),
        MarketplaceAccount.__table__.name: (
            MarketplaceAccount.__table__.c.workspace_id.in_(workspace_ids), "workspace_cabinet"),
        MarketplaceStore.__table__.name: (
            MarketplaceStore.__table__.c.marketplace_account_id.in_(account_ids), "cabinet_store"),
        MarketplaceConnection.__table__.name: (
            MarketplaceConnection.__table__.c.user_id == user_id, "connection_owner"),
        ApiCredential.__table__.name: (
            ApiCredential.__table__.c.connection_id.in_(connection_ids), "connection_secret_count_only"),
    }
    for child_name, child_column, parent_name in INDIRECT_OWNER_LINKS:
        child = Base.metadata.tables[child_name]
        parent = Base.metadata.tables[parent_name]
        parent_ids = select(parent.c.id).where(parent.c.user_id == user_id)
        explicit[child_name] = (child.c[child_column].in_(parent_ids), "reviewed_parent_owner_reference")
    policies = Base.metadata.tables["store_data_source_policies"]
    store_ids = select(MarketplaceStore.id).where(MarketplaceStore.marketplace_account_id.in_(account_ids))
    explicit[policies.name] = (policies.c.marketplace_store_id.in_(store_ids), "cabinet_store_policy")
    conflict_rules = {}

    def linked(table_name, links):
        """Reviewed owner links only; never generic reachability or delete authority."""
        table = tables[table_name]
        owned = [table.c[column].in_(ids) for column, ids in links]
        guards = [or_(table.c[column].is_(None), match) if table.c[column].nullable else match
                  for (column, _), match in zip(links, owned)]
        consistent = and_(or_(*owned), *guards)
        explicit[table_name] = (consistent, "consistent_owner_paths")
        conflict_rules[table_name] = and_(or_(*owned), not_(consistent))
        return select(table.c.id).where(consistent)

    # CSV ownership includes the direct owner, not merely optional parent FKs.
    # Raw product_ids remain diagnostic anchors: a conflicted Product must not
    # hide reachable child rows. This is not recursive deletion authority.
    user_ids = select(User.id).where(User.id == user_id)
    linked("products", (("user_id", user_ids), ("marketplace_account_id", account_ids)))
    for table_name in ("imported_product_rows", "imported_finance_rows"):
        linked(table_name, (("user_id", user_ids), ("marketplace_account_id", account_ids),
               ("marketplace_store_id", store_ids), ("product_id", product_ids)))

    linked("connection_verification_attempts", (("connection_id", connection_ids),
           ("credential_id", credential_ids), ("marketplace_account_id", account_ids)))
    linked("api_sync_states", (("marketplace_connection_id", connection_ids),
           ("marketplace_account_id", account_ids), ("marketplace_store_id", store_ids)))
    linked("marketplace_operations", (("marketplace_account_id", account_ids),
           ("marketplace_store_id", store_ids), ("product_id", product_ids)))
    linked("product_placements", (("product_id", product_ids),
           ("marketplace_store_id", store_ids), ("marketplace_account_id", account_ids)))
    linked("marketplace_price_observations", (("marketplace_account_id", account_ids),
           ("marketplace_store_id", store_ids), ("product_id", product_ids)))
    promotion_ids = linked("marketplace_promotion_observations", (
        ("marketplace_account_id", account_ids), ("product_id", product_ids)))
    linked("marketplace_promotion_store_evidence", (("promotion_observation_id", promotion_ids),
           ("marketplace_account_id", account_ids), ("marketplace_store_id", store_ids)))
    policy_ids = linked("protection_policies", (("marketplace_store_id", store_ids), ("product_id", product_ids)))
    linked("protection_tax_settings", (("policy_id", policy_ids),))
    linked("protection_additional_costs", (("policy_id", policy_ids),))
    linked("protection_evaluations", (("policy_id", policy_ids),
           ("marketplace_store_id", store_ids), ("product_id", product_ids)))
    linked("protection_action_states", (("policy_id", policy_ids), ("marketplace_store_id", store_ids),
           ("product_id", product_ids), ("execution_log_id", execution_ids)))
    # Confirmed by the creation routers: seller_id/reviewer_id are current_user.id,
    # unlike supplier_id/target_id, which refer to separate counterpart entities.
    for table_name, owner_column in (("deals", "seller_id"), ("supplier_reviews", "reviewer_id")):
        table = Base.metadata.tables[table_name]
        explicit[table_name] = (table.c[owner_column] == user_id, "reviewed_alternate_owner_reference")
    counts = []
    unclassified = []
    cross_party = []
    conflicts = []
    # Prevent autoflush of unrelated pending writes in an operator's session.
    with db.no_autoflush:
        connection = await db.connection()
        actual_tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        # Legacy/migration-only tables must not silently disappear from the report.
        unclassified.extend(sorted(set(actual_tables) - set(Base.metadata.tables)))
        for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
            if table.name in SHARED_REFERENCE_TABLES:
                continue
            parties = sorted(set(table.c.keys()) & {
                "referrer_id", "invitee_id", "referred_by_id", "supplier_id", "reviewer_id", "target_id",
            })
            if parties:
                cross_party.append({"table": table.name, "columns": parties})
            rule = explicit.get(table.name)
            if rule is None:
                owner_columns = [name for name in ("user_id", "owner_user_id") if name in table.c]
                if not owner_columns:
                    unclassified.append(table.name)
                    continue
                rule = (or_(*(table.c[name] == user_id for name in owner_columns)),
                        "direct_owner_reference")
            predicate, basis = rule
            count = await db.scalar(select(func.count()).select_from(table).where(predicate))
            counts.append({"table": table.name, "count": count, "basis": basis})
            if table.name in conflict_rules:
                conflict_count = await db.scalar(select(func.count()).select_from(table).where(conflict_rules[table.name]))
                conflicts.append({"table": table.name, "count": conflict_count})
    return {
        "format_version": 1,
        "read_only": True,
        "complete_erasure_ready": False,
        "account_closure_review": closure_review,
        "counts": counts,
        "schema_scope": "default_schema_tables",
        "unclassified_tables": sorted(unclassified),
        "cross_party_review": cross_party,
        "shared_reference_tables": sorted(SHARED_REFERENCE_TABLES),
        "conflicting_owner_paths": conflicts,
        "ownerless_review": OWNERLESS_REVIEW.copy(),
        "not_inspected": ["files", "backups", "queues", "external_processors", "retention_grounds",
                          "views", "non_default_schemas", "intra_owner_relationship_consistency",
                          "direct_owner_parent_consistency", "concurrent_lifecycle_state"],
        "warning": "Counts are not authorization to delete; indirect and shared references require review.",
    }
