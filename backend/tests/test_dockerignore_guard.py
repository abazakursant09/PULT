"""SECURITY-2D-3C1 — build-context hardening guards.

Static assertions that both Docker build contexts (./backend, ./frontend) exclude secrets/keys/DB/host
build-output and still keep the files each build actually needs. This is NOT proof of Docker semantics
(the real proof is the docker-build CI job building both images) — it just pins the pattern set so a future
edit cannot silently drop a secret rule or start excluding a required build input.
"""
from __future__ import annotations

import os

_HERE = os.path.dirname(__file__)
_BACKEND_DI = os.path.join(_HERE, "..", ".dockerignore")
_FRONTEND_DI = os.path.join(_HERE, "..", "..", "frontend", ".dockerignore")


def _lines(path):
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]


def test_backend_dockerignore_excludes_secrets_and_keeps_build_inputs():
    pats = _lines(_BACKEND_DI)
    # secrets / VCS / keys / DB / uploads must be excluded
    for required in (".env", ".env.*", ".git", "*.pem", "*.key", "*.db", "uploads/"):
        assert required in pats, f"backend/.dockerignore missing {required!r}"
    # must NOT exclude the files the backend image build/startup needs
    for keep in ("requirements.lock", "alembic.ini", "alembic", "alembic/", "main.py", "config.py",
                 "models", "models/", "routers", "routers/"):
        assert keep not in pats, f"backend/.dockerignore must not exclude build input {keep!r}"


def test_frontend_dockerignore_exists_excludes_secrets_and_host_output():
    assert os.path.exists(_FRONTEND_DI), "frontend/.dockerignore is missing"
    pats = _lines(_FRONTEND_DI)
    for required in (".env", ".env.*", ".git", "*.pem", "*.key", "node_modules", ".next", "*.tsbuildinfo"):
        assert required in pats, f"frontend/.dockerignore missing {required!r}"
    # must NOT exclude files npm ci / next build / next start need
    for keep in ("package.json", "package-lock.json", "next.config.js", "tsconfig.json",
                 "next-env.d.ts", "app", "app/", "components", "components/", "lib", "lib/",
                 "public", "public/", "middleware.ts", "middleware.*"):
        assert keep not in pats, f"frontend/.dockerignore must not exclude build input {keep!r}"
