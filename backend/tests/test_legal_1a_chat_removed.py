"""LEGAL-1A guard — the user-to-user "Биржа" chat is gone and cannot come back silently.

149-FZ art. 10.1 (ОРИ) exposure came from real user-to-user electronic messaging. This test
fails if any part of that surface reappears in production code: the router, the model, the table,
or any /api/chat/* endpoint.

Note: telegram_chat_id and the Telegram admin "chat id" are a DIFFERENT feature (user↔bot
notifications, not user↔user messaging) and are intentionally NOT covered here.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

import models  # registers all tables on Base.metadata
from database import Base
from models.user import User


_BACKEND = Path(__file__).resolve().parents[1]
# Production packages only — the alembic migration (which legitimately names the dropped table and
# columns in its up/down + docstring) and the tests are deliberately excluded.
_PROD_DIRS = ["routers", "services", "models", "schemas", "logic", "tasks"]
_FORBIDDEN = [
    r"\bChatMessage\b",
    r"\bchat_violations\b",
    r"\bchat_blocked\b",
    r"/api/chat\b",
    r"routers\.chat\b",
    r"\bchat\.router\b",
]


def _prod_py_files():
    files = list(_BACKEND.glob("*.py"))  # main.py, dependencies.py, ...
    for d in _PROD_DIRS:
        files += (_BACKEND / d).rglob("*.py")
    return files


def test_chat_message_model_is_gone():
    # The ChatMessage ORM model must not be importable or registered anywhere.
    assert not hasattr(models, "ChatMessage")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("models.chat_message")


def test_chat_router_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("routers.chat")


def test_chat_messages_table_not_in_metadata():
    assert "chat_messages" not in Base.metadata.tables


def test_no_chat_api_routes_are_mounted():
    import main

    paths = set(main.app.openapi()["paths"])
    offending = [p for p in paths if p.startswith("/api/chat")]
    assert offending == [], f"chat endpoints still mounted: {offending}"


def test_user_model_has_no_chat_moderation_columns():
    # LEGAL-1A drops these columns entirely — the model must not declare them anymore.
    assert not hasattr(User, "chat_violations")
    assert not hasattr(User, "chat_blocked")
    cols = set(User.__table__.columns.keys())
    assert "chat_violations" not in cols
    assert "chat_blocked" not in cols


def test_no_chat_tokens_in_production_code():
    patterns = [re.compile(p) for p in _FORBIDDEN]
    offenders = []
    for f in _prod_py_files():
        text = f.read_text(encoding="utf-8")
        for pat in patterns:
            if pat.search(text):
                offenders.append(f"{f.relative_to(_BACKEND)}: {pat.pattern}")
    assert offenders == [], "chat references left in production code:\n" + "\n".join(offenders)
