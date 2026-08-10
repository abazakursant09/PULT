"""SECURITY-2D-3C2 — backend Dockerfile non-root guards (static).

Pins the hardening invariants so a future edit cannot silently return the image to root or widen the
writable surface. NOT proof of runtime behaviour — the authoritative proof is the docker-build CI job that
builds the image, asserts Config.User == 10001:10001, and runs the writable-allowlist checks.
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(__file__)
_DOCKERFILE = os.path.join(_HERE, "..", "Dockerfile")


def _text():
    with open(_DOCKERFILE, encoding="utf-8") as f:
        return f.read()


def test_final_user_is_non_root():
    src = _text()
    users = re.findall(r"(?m)^\s*USER\s+(.+?)\s*$", src)
    assert users, "no USER directive — image runs as root"
    last = users[-1].strip()
    assert last not in ("root", "0", "0:0"), f"final USER is root: {last!r}"
    assert "10001" in last, f"final USER must be the fixed non-root uid 10001, got {last!r}"


def test_writable_surface_is_narrow():
    src = _text()
    # only /app/uploads is chowned to the app user; the source tree stays root-owned
    assert re.search(r"chown\s+10001:10001\s+/app/uploads", src), "must chown /app/uploads to 10001"
    assert not re.search(r"chown\s+-R\s+\S*\s*/app(\s|$)", src), "must NOT recursively chown all of /app"
    assert "chown -R 10001:10001 /app\n" not in src and "chown -R app:app /app" not in src
    assert "chmod -R 777" not in src and "chmod 777" not in src
    # source must NOT be copied with app ownership (that would make code writable)
    assert not re.search(r"COPY\s+--chown=(app|10001)\S*\s+\.\s+\.", src), "source COPY must stay root-owned"


def test_no_bytecode_write_and_cmd_preserved():
    src = _text()
    assert "PYTHONDONTWRITEBYTECODE=1" in src
    assert "alembic upgrade head" in src and "uvicorn main:app" in src   # migration + server preserved
    assert "sudo" not in src
