"""Transport admission for read-only Ozon/WB ingest, not arbitrary API use."""
from contextvars import ContextVar
from functools import wraps
from urllib.parse import urlsplit
from config import settings
from .errors import ExecutionError

_scope = ContextVar('ingest_read_scope', default=None)
_ROUTES = {
    'ozon': {'ozon_seller_base': {
        ('POST', '/v3/product/list'), ('POST', '/v3/product/info/list'),
        ('POST', '/v5/product/info/prices'), ('POST', '/v4/product/info/stocks'),
        ('POST', '/v2/posting/fbo/list'), ('POST', '/v3/posting/fbs/list'),
        ('POST', '/v3/finance/transaction/list'), ('POST', '/v1/returns/list'),
        ('GET', '/v1/actions'), ('POST', '/v1/actions/products')}},
    'wildberries': {
        'wb_content_base': {('POST', '/content/v2/get/cards/list')},
        'wb_prices_base': {('GET', '/api/v2/list/goods/filter')},
        'wb_statistics_base': {('GET', '/api/v1/supplier/orders'), ('GET', '/api/v1/supplier/sales')},
        'wb_finance_base': {('POST', '/api/finance/v1/sales-reports/detailed')},
        'wb_calendar_base': {('GET', '/api/v1/calendar/promotions'),
                             ('GET', '/api/v1/calendar/promotions/details'),
                             ('GET', '/api/v1/calendar/promotions/nomenclatures')}},
}


def read_only_ingest(marketplace):
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            if settings.marketplace_read_only is False:
                return await fn(*args, **kwargs)
            # Shared lifetime also invalidates scope inherited by background tasks.
            parent = _scope.get()
            if parent is not None and not _active(parent):
                raise ExecutionError.guard('INGEST_READ_ONLY', 'expired ingest scope')
            lifetime = [marketplace, True, parent]
            token = _scope.set(lifetime)
            try:
                return await fn(*args, **kwargs)
            finally:
                lifetime[1] = False
                _scope.reset(token)
        return wrapped
    return decorate


def _active(scope):
    while scope is not None:
        if not scope[1]:
            return False
        scope = scope[2]
    return True


def _origin_only(base):
    try:
        url = urlsplit(base)
        return (url.scheme in ('https', 'http') and bool(url.hostname)
                and url.path == '' and not url.query and not url.fragment
                and not url.username and not url.password and '?' not in base and '#' not in base)
    except ValueError:
        return False


def admit_ingest_read(base, method, path):
    scope = _scope.get()
    if scope is None:
        return
    marketplace = scope[0]
    if _active(scope) and _origin_only(base) and any(base == getattr(settings, key).rstrip('/') and (method, path) in routes
                      for key, routes in _ROUTES.get(marketplace, {}).items()):
        return
    # Report-task creation and related poll/download are deliberately not admitted yet.
    raise ExecutionError.guard('INGEST_READ_ONLY', 'operation not admitted for read-only ingest')
