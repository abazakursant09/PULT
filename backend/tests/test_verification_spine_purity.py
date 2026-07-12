"""
F1.2b-b — the verification spine must stay marketplace-agnostic.

PULT is not a Wildberries system. The spine is allowed to know that adapters exist; it is
not allowed to know what Wildberries is. These tests are the mechanical guard on that line,
because the failure mode is gradual: one `if marketplace == ...` at a time, until the
"shared framework" is a WB framework with a slot for everyone else.

Ozon and Yandex must later arrive as sibling files under adapters/ — changing no schema, no
taxonomy, no projection, no rollup and no public response.
"""
import ast
import asyncio
import uuid
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from models.api_credential import ApiCredential
from models.connection_verification_attempt import ConnectionVerificationAttempt
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.user import User
from models.workspace import Workspace

from services.marketplace import credential_vault
from services.marketplace.verification import adapters, runner
from services.marketplace.verification.adapters import ADAPTERS, get_adapter
from services.marketplace.verification.service import NullVerifier
from services.marketplace.verification.taxonomy import VerificationOutcome

VERIFICATION_DIR = Path(__file__).resolve().parents[1] / "services" / "marketplace" / "verification"

# Files that make up the COMMON spine — everything except the adapters package.
SPINE_FILES = sorted(p for p in VERIFICATION_DIR.glob("*.py"))

# Marketplace knowledge that must never appear in the spine.
FORBIDDEN_LITERALS = (
    "wildberries", "ozon", "yandex", "wb_", "seller-info", "/ping",
    "wildberries.ru", "ozon.ru", "x-ratelimit", "item-retry-after",
    "api-key", "client-id", "authorization",
)


def _run(c):
    return asyncio.run(c)


async def _orm_session():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _connection(db, marketplace):
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@b.com", name="U",
                hashed_password="x")
    db.add(user)
    ws = Workspace(id=str(uuid.uuid4()), owner_user_id=user.id, created_at=datetime.utcnow())
    db.add(ws)
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=ws.id,
                             marketplace=marketplace, identity_status="unverified")
    db.add(acc)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=user.id, marketplace=marketplace,
        status="connected", scopes=["prices"], workspace_id=ws.id,
        marketplace_account_id=acc.id,
    )
    db.add(conn)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="prices",
                         secret_enc=credential_vault.encrypt("t0ken")))
    await db.commit()
    return user, conn


# ── A. no marketplace knowledge in the spine ─────────────────────────────────

def test_spine_files_exist_and_are_scanned():
    names = {p.name for p in SPINE_FILES}
    assert {"taxonomy.py", "projection.py", "service.py", "runner.py",
            "transport.py"} <= names


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Every string Constant that is a docstring, by identity."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


@pytest.mark.parametrize("path", SPINE_FILES, ids=lambda p: p.name)
def test_spine_contains_no_marketplace_literal(path):
    """Hosts, paths, header names and marketplace names belong in adapters/, not here.

    Docstrings are exempt: prose may NAME a marketplace to explain WHY a rule exists —
    that is documentation, not knowledge the code acts on. Only executable string
    constants are forbidden from knowing a marketplace.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    exempt = _docstring_nodes(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in exempt:
            for forbidden in FORBIDDEN_LITERALS:
                assert forbidden not in node.value.lower(), (
                    f"{path.name} carries the marketplace literal {forbidden!r} in code — "
                    "it belongs in an adapter"
                )


@pytest.mark.parametrize("path", SPINE_FILES, ids=lambda p: p.name)
def test_spine_has_no_marketplace_branch(path):
    """No `if marketplace == "..."`. Dispatch is a registry lookup, by design."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            src = ast.unparse(node).lower()
            if "marketplace" in src:
                for forbidden in ("wildberries", "ozon", "yandex"):
                    assert forbidden not in src, f"{path.name}: marketplace branch {src!r}"


def test_spine_never_imports_an_adapter_module_directly():
    """It resolves adapters through the registry — never by importing one."""
    for path in SPINE_FILES:
        imports = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                   if ln.startswith(("import ", "from "))]
        for ln in imports:
            assert "adapters.wildberries" not in ln, f"{path.name} imports the WB adapter"


# ── B. registry dispatch ─────────────────────────────────────────────────────

def test_adapter_selection_is_a_registry_lookup():
    assert isinstance(ADAPTERS, dict)
    assert "wildberries" in ADAPTERS
    assert get_adapter("wildberries") is ADAPTERS["wildberries"]


def test_unknown_marketplace_has_no_adapter():
    """An unregistered marketplace is not a special case — just an absent entry.

    (Yandex used to be the example here. It has an adapter now, which is exactly how this
    is supposed to go: a marketplace joins by appearing in the registry, and nothing else
    in the spine notices.)
    """
    assert get_adapter("megamarket") is None
    assert get_adapter("aliexpress") is None
    assert get_adapter("") is None


def test_registry_module_is_the_only_common_place_naming_an_adapter():
    """adapters/__init__ may name them; nothing else in the spine may."""
    src = (VERIFICATION_DIR / "adapters" / "__init__.py").read_text(encoding="utf-8").lower()
    for marketplace in ("wildberries", "ozon", "yandex"):
        assert marketplace in src             # the registry is exactly where it belongs


@pytest.mark.parametrize("marketplace,module", [
    ("wildberries", "wildberries.py"),
    ("ozon", "ozon.py"),
    ("yandex", "yandex.py"),
])
def test_each_marketplace_lives_in_exactly_one_adapter_module(marketplace, module):
    """No adapter may know another. Three adapters is where a "shared" framework usually
    starts sprouting cross-references — one marketplace borrowing another's status codes,
    one importing another's helper. Each may only know itself.
    """
    adapters_dir = VERIFICATION_DIR / "adapters"
    for path in adapters_dir.glob("*.py"):
        if path.name in (module, "__init__.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        exempt = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in exempt:
                assert marketplace not in node.value.lower(), (
                    f"{path.name} carries a {marketplace} literal in code"
                )


def test_adapters_do_not_import_one_another():
    adapters_dir = VERIFICATION_DIR / "adapters"
    siblings = {"wildberries", "ozon", "yandex"}
    for path in adapters_dir.glob("*.py"):
        if path.name == "__init__.py":        # the registry is allowed to import them all
            continue
        own = path.stem
        imports = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                   if ln.startswith(("import ", "from "))]
        for other in siblings - {own}:
            assert not any(other in ln for ln in imports), \
                f"{path.name} imports the {other} adapter"


def test_marketplace_without_an_adapter_yields_verification_unsupported():
    async def go():
        db = await _orm_session()
        user, conn = await _connection(db, "megamarket")

        _c, cred, result = await runner.verify_credential(
            db, user_id=user.id, connection_id=conn.id, scope="prices")

        assert result.outcome is VerificationOutcome.VERIFICATION_UNSUPPORTED
        await db.refresh(cred)
        assert cred.verification_status == "unverified"   # honest silence, not a verdict

        attempt = (await db.execute(
            sa.select(ConnectionVerificationAttempt))).scalar_one()
        assert attempt.outcome == "verification_unsupported"
    _run(go())


def test_null_verifier_never_creates_verified_state():
    result = _run(NullVerifier().verify(marketplace="megamarket", scope="prices"))
    assert result.outcome is VerificationOutcome.VERIFICATION_UNSUPPORTED
    assert result.outcome is not VerificationOutcome.VERIFIED


# ── C. the adapter contract carries no database ──────────────────────────────

def test_adapters_cannot_touch_the_database():
    """An adapter that cannot see a session cannot persist a verdict by accident."""
    base = (VERIFICATION_DIR / "adapters" / "base.py").read_text(encoding="utf-8")
    for forbidden in ("AsyncSession", "sqlalchemy", "record_attempt", "ApiCredential",
                      "MarketplaceConnection"):
        assert forbidden not in base, f"the adapter contract exposes {forbidden}"

    for path in (VERIFICATION_DIR / "adapters").glob("*.py"):
        imports = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                   if ln.startswith(("import ", "from "))]
        for ln in imports:
            assert "sqlalchemy" not in ln, f"{path.name} imports sqlalchemy"
            assert "models" not in ln, f"{path.name} imports an ORM model"
