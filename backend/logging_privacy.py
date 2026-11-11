"""Query minimization for normal Uvicorn HTTP access records, not error tracebacks.

Keep method/path/status/client observability without logging verification tokens
or other query values. Only the LogRecord is changed, never the ASGI request.
"""
from __future__ import annotations

import logging

_ACCESS_MESSAGE = '%s - "%s %s HTTP/%s" %d'


class QuerylessAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != 'uvicorn.access':
            return True
        args = record.args
        # Fail closed for an unfamiliar access-record shape rather than leaking
        # its arbitrary message/arguments. Error/application loggers are untouched.
        if record.msg != _ACCESS_MESSAGE or not isinstance(args, tuple) or len(args) != 5:
            return False
        if not all(isinstance(value, str) for value in args[:4]) or not isinstance(args[4], int):
            return False
        client, method, path, version, status = args
        record.args = (client, method, path.partition('?')[0], version, status)
        return True


def install_access_log_redaction() -> None:
    # Attach to the logger so migration-time handler reconfiguration cannot
    # silently remove the filter. Repeated app imports/installations are safe.
    logger = logging.getLogger('uvicorn.access')
    if not any(isinstance(item, QuerylessAccessLogFilter) for item in logger.filters):
        logger.addFilter(QuerylessAccessLogFilter())
