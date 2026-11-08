"""Test-owned main.app, loopback only; capture OWN reset tokens, never send mail.

This is not an application route, real SMTP proof or production startup mode.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import socket
import sys

probe = runpy.run_path(str(Path(__file__).with_name("pg-database.py")))
root = probe["owned_root"]()
database = os.environ["PULT_E2E_DATABASE"]
assert database in (probe["SOURCE"], probe["TARGET"])
probe["connection"](database).close()  # refuse a different cluster BEFORE app startup
secret = os.environ["PULT_E2E_SESSION_SECRET"]
assert len(secret) >= 32
password = (root / "password.txt").read_text().strip()
backend = Path(__file__).resolve().parents[2] / "backend"
assert not (backend / ".env").exists(), "developer .env forbidden"
keep = {key: value for key, value in os.environ.items() if key.upper() in {
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT",
    "LOCALAPPDATA", "PYTHONPATH",
}}
os.environ.clear()
os.environ.update(keep)
os.environ.update({
    "APP_ENV": "development", "SECRET_KEY": secret,
    "DATABASE_URL": f"postgresql+asyncpg://pult_e2e:{password}@127.0.0.1:55432/{database}",
    "FRONTEND_URL": "http://127.0.0.1:3097", "API_URL": "http://127.0.0.1:8097",
    "SMTP_HOST": "", "SENTRY_DSN": "", "TELEGRAM_BOT_TOKEN": "",
    "BILLING_ENABLED": "false", "API_DATA_SYNC_ENABLED": "false",
    "AUTOMATION_ENABLED": "false", "PYTHONDONTWRITEBYTECODE": "1",
})
sys.dont_write_bytecode = True
sys.path.insert(0, str(backend))
os.chdir(backend)
connect, connect_ex = socket.socket.connect, socket.socket.connect_ex


def check(address):
    if not isinstance(address, tuple) or str(address[0]) not in {"127.0.0.1", "::1", "localhost"}:
        raise PermissionError("browser PG acceptance permits loopback only")


def guarded_connect(sock, address):
    check(address)
    return connect(sock, address)


def guarded_connect_ex(sock, address):
    check(address)
    return connect_ex(sock, address)


socket.socket.connect, socket.socket.connect_ex = guarded_connect, guarded_connect_ex

from routers import auth  # noqa: E402


async def capture_reset(email, name, token):
    assert re.fullmatch(r"browser-[a-z]+-[a-f0-9-]+@example.com", email), "not an owned test account"
    filename = root / ("reset-" + hashlib.sha256(email.encode()).hexdigest() + ".json")
    with filename.open("x", encoding="utf-8") as stream:
        json.dump({"email": email, "token": token, "sent": False}, stream)
    return False  # never claim SMTP delivery


auth.send_password_reset_email = capture_reset

import uvicorn  # noqa: E402

uvicorn.run("main:app", host="127.0.0.1", port=8097, access_log=False)
