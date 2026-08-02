"""LEGAL-1A guard — the user-to-user "Биржа" chat is gone and cannot come back silently.

149-FZ art. 10.1 (ОРИ) exposure came from real user-to-user electronic messaging. This test
fails if any part of that surface reappears in production code: the router, the model, the table,
or any /api/chat/* endpoint.

Note: telegram_chat_id and the Telegram admin "chat id" are a DIFFERENT feature (user↔bot
notifications, not user↔user messaging) and are intentionally NOT covered here.
"""
from __future__ import annotations

import importlib

import pytest

import models  # registers all tables on Base.metadata
from database import Base


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
