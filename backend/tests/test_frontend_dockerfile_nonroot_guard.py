"""SECURITY-2D-3C3 — frontend Dockerfile non-root guards (static).

Pins the frontend hardening invariants so a future edit cannot silently return the image to root or widen
the writable surface. NOT proof of runtime behaviour — the authoritative proof is the docker-build CI job
(Config.User, id, fs allow/deny, and an HTTP smoke of the final non-root image).
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(__file__)
_FE = os.path.join(_HERE, "..", "..", "frontend", "Dockerfile")


def _text():
    with open(_FE, encoding="utf-8") as f:
        return f.read()


def test_final_user_is_non_root():
    src = _text()
    users = re.findall(r"(?m)^\s*USER\s+(.+?)\s*$", src)
    assert users, "no USER directive — frontend runs as root"
    last = users[-1].strip()
    assert last not in ("root", "0", "0:0"), f"final USER is root: {last!r}"
    assert last in ("node", "1000", "1000:1000"), f"final USER must be node/1000, got {last!r}"


def test_writable_surface_is_narrow_and_telemetry_off():
    src = _text()
    assert "NEXT_TELEMETRY_DISABLED=1" in src
    # only .next/cache is re-owned to the runtime user
    assert re.search(r"chown\s+-R\s+node:node\s+\.next/cache", src), "must chown .next/cache to node"
    # must NOT recursively re-own all of /app or the whole .next
    assert not re.search(r"chown\s+-R\s+\S*\s*/app(\s|$)", src), "must not chown -R /app"
    assert not re.search(r"chown\s+-R\s+\S*\s*\.next(\s|$)", src), "must not chown -R the whole .next"
    assert "chmod -R 777" not in src and "chmod 777" not in src
    # source must NOT be copied with node ownership (would make code writable)
    assert not re.search(r"COPY\s+--chown=(node|1000)\S*\s+\.\s+\.", src), "source COPY must stay root-owned"


def test_single_stage_and_cmd_preserved():
    src = _text()
    assert src.count("\nFROM ") + src.startswith("FROM ") == 1 or src.count("FROM ") == 1  # single-stage
    assert "node:20-alpine" in src                    # base image unchanged (no digest/major change here)
    assert re.search(r'CMD\s+\[\s*"npm"\s*,\s*"run"\s*,\s*"start"\s*\]', src)   # start preserved
    assert "sudo" not in src
