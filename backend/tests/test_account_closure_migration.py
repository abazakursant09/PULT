"""Disposable SQLite expansion/guarded contraction; native PG is a separate gate."""
from pathlib import Path
import shutil

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
PARENT = 'ymh1a2b3c4d01'
REV = 'acl1a2b3c4d01'
FIELDS = ('closure_policy_version', 'closure_started_at',
          'closure_restore_requested_at', 'closure_purge_started_at')


def config():
    cfg = Config(str(ROOT/'alembic.ini'))
    cfg.set_main_option('script_location', str(ROOT/'alembic'))
    return cfg


@pytest.fixture(scope='module')
def prior(tmp_path_factory):
    path = tmp_path_factory.mktemp('closure-prior')/'prior.sqlite'
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv('ALEMBIC_DATABASE_URL', 'sqlite+aiosqlite:///'+path.as_posix())
        command.upgrade(config(), PARENT)
    engine = sa.create_engine('sqlite:///'+path.as_posix())
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO users(id,email,name,hashed_password) VALUES "
                                 "('synthetic','synthetic@example.com','synthetic','not-a-password')"))
    finally:
        engine.dispose()
    return path


@pytest.fixture
def migrated(prior, tmp_path, monkeypatch):
    path=tmp_path/'closure.sqlite'
    shutil.copyfile(prior,path)
    monkeypatch.setenv('ALEMBIC_DATABASE_URL','sqlite+aiosqlite:///'+path.as_posix())
    command.upgrade(config(),REV)
    engine=sa.create_engine('sqlite:///'+path.as_posix())
    try:
        yield engine
    finally:
        engine.dispose()


def test_single_pinned_head():
    script=ScriptDirectory.from_config(config())
    assert script.get_heads()==[REV]
    assert script.get_revision(REV).down_revision==PARENT


def test_upgrade_preserves_legacy_user_without_backfill(migrated):
    from models.user import User
    with migrated.connect() as conn:
        row=conn.execute(sa.text('SELECT email,'+','.join(FIELDS)+' FROM users')).one()
        assert row[0]=='synthetic@example.com'
        assert tuple(row[1:])==(None,None,None,None)
    for name in FIELDS:
        assert User.__table__.c[name].nullable


def test_empty_lifecycle_downgrade_preserves_user(migrated):
    command.downgrade(config(),PARENT)
    assert not set(FIELDS)&{c['name'] for c in sa.inspect(migrated).get_columns('users')}
    with migrated.connect() as conn:
        assert conn.execute(sa.text('SELECT email FROM users')).scalar_one()=='synthetic@example.com'


@pytest.mark.parametrize('field',FIELDS)
def test_downgrade_refuses_any_lifecycle_evidence(migrated,field):
    value='normal-close-30d-v1' if field==FIELDS[0] else '2026-11-10 12:00:00'
    with migrated.begin() as conn:
        conn.execute(sa.text('UPDATE users SET '+field+'=:v'),{'v':value})
    with pytest.raises(RuntimeError,match='Closure evidence exists'):
        command.downgrade(config(),PARENT)
    assert set(FIELDS)<={c['name'] for c in sa.inspect(migrated).get_columns('users')}
    with migrated.connect() as conn:
        assert conn.execute(sa.text('SELECT '+field+' FROM users')).scalar_one()==value


def test_offline_downgrade_cannot_skip_evidence_guard():
    with pytest.raises(RuntimeError,match='offline downgrade'):
        command.downgrade(config(),REV+':'+PARENT,sql=True)
