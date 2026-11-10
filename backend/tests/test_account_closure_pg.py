"""Native PG row-lock proof; requires an explicitly supplied synthetic test DB."""
import asyncio
import os
from datetime import datetime, timezone

import pytest

from fastapi import HTTPException, Response
from sqlalchemy import select

from models.user import User
from routers.auth import verify_email
from services.account_closure_policy import POLICY_VERSION
from test_token_version_pg import _pg_engine_or_skip, _reset_and_seed


def test_pg_one_recovery_link_cannot_be_consumed_twice():
    if not os.environ.get('PULT_TEST_PG_URL'):
        pytest.skip('Explicit disposable PULT_TEST_PG_URL required; never use application DB fallback')
    async def scenario():
        engine = _pg_engine_or_skip()
        try:
            sessions, uid = await _reset_and_seed(engine, ver=7, deleted=True)
            async with sessions() as db:
                user = await db.get(User, uid)
                now = datetime.now(timezone.utc)
                user.closure_policy_version = POLICY_VERSION
                user.closure_started_at = now
                user.closure_restore_requested_at = now
                user.is_verified = False
                user.verification_token = 'synthetic-single-use-recovery'
                await db.commit()

            async def consume():
                response = Response()
                async with sessions() as db:
                    try:
                        value = await verify_email(response, 'synthetic-single-use-recovery', db)
                        assert value.login_required is True
                        assert 'Max-Age=0' in response.headers.get('set-cookie', '')
                        return 'recovered'
                    except HTTPException as exc:
                        assert exc.status_code == 400
                        return 'rejected'

            assert sorted(await asyncio.gather(consume(), consume())) == ['recovered', 'rejected']
            async with sessions() as db:
                user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
                assert user.deleted_at is None
                assert user.verification_token is None
                assert user.closure_restore_requested_at is None
                assert user.token_version == 7
        finally:
            await engine.dispose()
    asyncio.run(scenario())
