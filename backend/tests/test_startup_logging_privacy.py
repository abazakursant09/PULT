"""Real logging controls in isolated processes; no seller DB, mail or cloud access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

BACKEND = Path(__file__).resolve().parents[1]
COMMON = f"BACKEND = {str(BACKEND)!r}\n" + r'''
import ast, io, json, logging, logging.config
from pathlib import Path
from types import SimpleNamespace
import uvicorn

async def empty_app(scope, receive, send):
    pass

def migration_logging():
    # Execute the ACTUAL env.py logging block, not migrations or model imports.
    source = Path(BACKEND, 'alembic', 'env.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    blocks = [node for node in tree.body if isinstance(node, ast.If) and any(
        isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
        and item.func.id == 'fileConfig' for item in ast.walk(node))]
    assert len(blocks) == 1
    block = ast.Module(body=blocks, type_ignores=[])
    exec(compile(block, str(source), 'exec'), {
        'fileConfig': logging.config.fileConfig,
        'config': SimpleNamespace(config_file_name=str(Path(BACKEND, 'alembic.ini'))),
    })
'''


def _child(body: str) -> dict:
    env = dict(os.environ)
    env['PYTHONPATH'] = str(BACKEND) + os.pathsep + env.get('PYTHONPATH', '')
    result = subprocess.run(
        [sys.executable, '-B', '-c', COMMON + '\n' + textwrap.dedent(body)],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=35,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.strip())


def test_migration_keeps_server_and_application_errors_enabled():
    result = _child('''
        uvicorn.Config(empty_app, log_level='info')
        names = ('uvicorn.error', 'uvicorn.access', 'services.email', 'db')
        loggers = [logging.getLogger(name) for name in names]
        migration_logging()
        assert all(not logger.disabled for logger in loggers)
        assert all(logger.isEnabledFor(logging.ERROR) for logger in loggers)
        sink = io.StringIO()
        loggers[2].addHandler(logging.StreamHandler(sink))
        loggers[2].error('PULT_DIAGNOSTIC_CONTROL')
        assert 'PULT_DIAGNOSTIC_CONTROL' in sink.getvalue()
        print(json.dumps({'errors_enabled': True}))
    ''')
    assert result['errors_enabled'] is True


@pytest.mark.parametrize('path', [
    '/api/auth/verify-email?token=SYNTHETIC_SECRET',
    '/api/auth/verify-email?token=SYNTHETIC_SECRET&token=SECOND_SECRET',
    '/api/health?unused=VALUE&token=ENCODED%2FSECRET',
    '/api/health',
])
def test_access_filter_removes_query_only_and_preserves_fields(path):
    result = _child(f'''
        from logging_privacy import QuerylessAccessLogFilter
        args = ('127.0.0.1:1234', 'GET', {path!r}, '1.1', 400)
        original = args
        record = logging.LogRecord('uvicorn.access', logging.INFO, 'test', 1,
            '%s - "%s %s HTTP/%s" %d', args, None)
        assert QuerylessAccessLogFilter().filter(record) is True
        assert record.args == (args[0], args[1], args[2].partition('?')[0], args[3], args[4])
        assert args == original  # The request's immutable input was not rewritten.
        assert '?' not in record.getMessage()
        assert 'SYNTHETIC_SECRET' not in record.getMessage()
        print(json.dumps({{'query_removed': True}}))
    ''')
    assert result['query_removed'] is True


def test_access_filter_is_idempotent_and_unknown_format_fails_closed():
    result = _child('''
        from logging_privacy import QuerylessAccessLogFilter, install_access_log_redaction
        install_access_log_redaction()
        install_access_log_redaction()
        access = logging.getLogger('uvicorn.access')
        assert sum(isinstance(item, QuerylessAccessLogFilter) for item in access.filters) == 1
        record = logging.LogRecord('uvicorn.access', logging.INFO, 'test', 1,
            'Unexpected path?token=SYNTHETIC_SECRET', (), None)
        assert access.filter(record) is False
        assert not any(isinstance(item, QuerylessAccessLogFilter)
                       for item in logging.getLogger('uvicorn.error').filters)
        migration_logging()
        migration_logging()
        assert sum(isinstance(item, QuerylessAccessLogFilter) for item in access.filters) == 1
        assert not access.disabled
        print(json.dumps({'fail_closed': True}))
    ''')
    assert result['fail_closed'] is True


@pytest.mark.parametrize('args', [(), ('client', 'GET'), ('client', 'GET', None, '1.1', 200),
                                  ('client', 'GET', '/path?token=SYNTHETIC', '1.1', '200')])
def test_malformed_access_arguments_are_dropped_without_logging_errors(args):
    result = _child(f'''
        from logging_privacy import QuerylessAccessLogFilter
        record = logging.LogRecord('uvicorn.access', logging.INFO, 'test', 1,
            '%s - "%s %s HTTP/%s" %d', {args!r}, None)
        assert QuerylessAccessLogFilter().filter(record) is False
        record.name = 'uvicorn.error'
        original = record.args
        assert QuerylessAccessLogFilter().filter(record) is True
        assert record.args == original
        print(json.dumps({{'malformed_dropped':True}}))
    ''')
    assert result['malformed_dropped'] is True


def test_main_installs_filter_before_startup():
    import ast
    tree = ast.parse((BACKEND / 'main.py').read_text(encoding='utf-8'))
    calls = [node for node in tree.body if isinstance(node, ast.Expr)
             and isinstance(node.value, ast.Call)
             and isinstance(node.value.func, ast.Name)
             and node.value.func.id == 'install_access_log_redaction']
    assert len(calls) == 1
    lifespan = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'lifespan')
    assert calls[0].lineno < lifespan.lineno


def test_real_http_keeps_query_and_positive_access_and_error_logs():
    result = _child(r'''
        import socket, threading, time, urllib.request
        from logging_privacy import install_access_log_redaction
        query = 'token=SYNTHETIC_NOT_VALID&other=value%20with%20space'
        async def app(scope, receive, send):
            assert scope['type'] == 'http'
            body = json.dumps({'query': scope['query_string'].decode('ascii')}).encode()
            await send({'type':'http.response.start', 'status':200, 'headers':[(b'content-type',b'application/json')]})
            await send({'type':'http.response.body', 'body':body})
        config = uvicorn.Config(app, host='127.0.0.1', port=0, log_level='info', lifespan='off')
        install_access_log_redaction()
        access_sink, error_sink = io.StringIO(), io.StringIO()
        logging.getLogger('uvicorn.access').handlers = [logging.StreamHandler(access_sink)]
        logging.getLogger('uvicorn.error').handlers = [logging.StreamHandler(error_sink)]
        migration_logging()
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        port = sock.getsockname()[1]
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.started
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f'http://127.0.0.1:{port}/api/auth/verify-email?{query}', timeout=5) as response:
                assert json.load(response)['query'] == query
            logging.getLogger('uvicorn.error').error('PULT_DIAGNOSTIC_CONTROL')
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
        assert not thread.is_alive()
        access = access_sink.getvalue()
        assert 'GET /api/auth/verify-email HTTP/1.1' in access and '200' in access
        assert '?' not in access and 'SYNTHETIC_NOT_VALID' not in access
        assert 'PULT_DIAGNOSTIC_CONTROL' in error_sink.getvalue()
        print(json.dumps({'query_received_unchanged':True,'positive_redacted_access_log':True,'error_log_enabled':True}))
    ''')
    assert all(result.values())
