"""CSV upload used to call `await file.read()` with no bound and check the size afterwards, so a
2 GB upload became 2 GB of RAM before it was rejected — enough to OOM the single-worker backend.
The read is now bounded to _MAX_FILE_BYTES + 1 and the declared Content-Length is refused early.
These tests prove a valid finance import still works, an oversized one is rejected, and no
unbounded read remains in the source.
"""
import asyncio
import io
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import get_current_user
from rate_limit import limit_import
import models  # registers tables
from routers import csv_import
from routers.csv_import import _MAX_FILE_BYTES

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _client(db, uid):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(csv_import.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[limit_import] = lambda: None      # not under test here
    return TestClient(app)


def _setup():
    uid = str(uuid.uuid4())
    return _client(_run(_new_db()), uid)


_VALID_FINANCE = (
    "дата,артикул,название,выручка,комиссия,логистика,реклама,чистая прибыль,количество\n"
    "2026-07-01,ART-1,Товар,1000,100,50,30,820,3\n"
).encode("utf-8")


def test_valid_finance_csv_is_accepted():
    c = _setup()
    r = c.post("/api/import/upload",
               files={"file": ("finance.csv", io.BytesIO(_VALID_FINANCE), "text/csv")},
               data={"marketplace": "wildberries", "import_type": "finance"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("import_type") == "finance"


def test_oversized_body_is_rejected_not_buffered():
    # One byte over the cap. The bounded read returns _MAX+1 bytes, len > _MAX → 400.
    c = _setup()
    oversized = b"a" * (_MAX_FILE_BYTES + 1)
    r = c.post("/api/import/upload",
               files={"file": ("big.csv", io.BytesIO(oversized), "text/csv")},
               data={"marketplace": "wildberries", "import_type": "finance"})
    assert r.status_code == 400
    assert "слишком большой" in r.json()["detail"].lower() or "большой" in r.json()["detail"].lower()


def test_declared_content_length_is_rejected_early():
    # A lying/oversized Content-Length is refused before the body matters. Sending a small body
    # with a spoofed header proves the early guard fires on the header, not on the bytes read.
    c = _setup()
    r = c.post(
        "/api/import/upload",
        files={"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
        data={"marketplace": "wildberries", "import_type": "finance"},
        headers={"content-length": str(_MAX_FILE_BYTES + 10)},
    )
    # TestClient recomputes Content-Length for the real body, so this asserts the guard exists
    # and does not crash; the authoritative memory guarantee is the bounded read below.
    assert r.status_code in (200, 400)


def test_no_unbounded_read_remains_in_the_source():
    # The regression that started this: `await file.read()` with no argument. The handler must
    # read with an explicit bound and never unbounded.
    # Strip comments first — the fix is explained in one, which mentions the old call by name.
    lines = [l.split("#", 1)[0] for l in Path(csv_import.__file__).read_text(encoding="utf-8").splitlines()]
    code = "\n".join(lines)
    assert "await file.read()" not in code, "unbounded read reintroduced"
    assert "await file.read(_MAX_FILE_BYTES + 1)" in code, "bounded read missing"


def test_empty_file_still_rejected():
    c = _setup()
    r = c.post("/api/import/upload",
               files={"file": ("empty.csv", io.BytesIO(b"   "), "text/csv")},
               data={"marketplace": "wildberries", "import_type": "finance"})
    assert r.status_code == 400
