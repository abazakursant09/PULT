import asyncio
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from config import settings
from services.marketplace.ingest_read_guard import read_only_ingest, admit_ingest_read
from services.marketplace.errors import ExecutionError
from services.marketplace.base_client import BaseMarketplaceClient
import httpx


class Tests(unittest.TestCase):
    def test_real_ingest_entry_installs_guard(self):
        from services.marketplace.ingest import ozon
        async def forbidden(*args, **kwargs):
            await BaseMarketplaceClient(settings.ozon_seller_base).request(
                'POST', '/v1/product/import/prices', token='synthetic')
        with patch.object(settings, 'marketplace_read_only', True), \
             patch.object(ozon, '_page_products', forbidden), \
             patch.object(httpx, 'AsyncClient') as transport:
            with self.assertRaises(ExecutionError):
                asyncio.run(ozon.fetch_and_persist_page(
                    None, SimpleNamespace(data_type='products'), 'synthetic', 'synthetic'))
            transport.assert_not_called()

    def run_scope(self, mp, base, method, path):
        @read_only_ingest(mp)
        async def op():
            admit_ingest_read(base, method, path)
        with patch.object(settings, 'marketplace_read_only', True):
            asyncio.run(op())

    def test_allowed(self):
        self.run_scope('ozon', settings.ozon_seller_base, 'POST', '/v3/product/info/list')
        self.run_scope('wildberries', settings.wb_finance_base, 'POST', '/api/finance/v1/sales-reports/detailed')

    def test_denied(self):
        for mp, base, method, path in [
            ('wildberries', settings.wb_advert_base, 'GET', '/adv/v0/pause'),
            ('wildberries', settings.wb_analytics_base, 'POST', '/api/v1/warehouse_remains'),
            ('ozon', settings.ozon_seller_base, 'POST', '/v1/product/import/prices'),
            ('ozon', 'https://foreign.invalid', 'POST', '/v3/product/info/list'),
            ('ozon', settings.ozon_seller_base, 'GET', '/v3/product/info/list'),
            ('ozon', settings.ozon_seller_base, 'POST', '/unknown'),
        ]:
            with self.subTest(path=path), self.assertRaises(ExecutionError):
                self.run_scope(mp, base, method, path)

    def test_transport_denies_before_http(self):
        @read_only_ingest('wildberries')
        async def op():
            await BaseMarketplaceClient(settings.wb_advert_base).request('GET', '/adv/v0/pause', token='synthetic')
        with patch.object(settings, 'marketplace_read_only', True), patch.object(httpx, 'AsyncClient') as transport:
            with self.assertRaises(ExecutionError):
                asyncio.run(op())
            transport.assert_not_called()

    def test_poisoned_configured_origins_denied(self):
        for suffix in ('/v1/product/import/prices#', '?query=', '#', '/unexpected'):
            base = 'https://api-seller.ozon.ru' + suffix
            with self.subTest(suffix=suffix), patch.object(settings, 'ozon_seller_base', base):
                with self.assertRaises(ExecutionError):
                    self.run_scope('ozon', base, 'POST', '/v3/product/list')

    def test_nested_child_cannot_outlive_parent_or_reenter(self):
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            @read_only_ingest('ozon')
            async def child():
                entered.set()
                await release.wait()
                with self.assertRaises(ExecutionError):
                    admit_ingest_read(settings.ozon_seller_base, 'POST', '/v3/product/list')
                with self.assertRaises(ExecutionError):
                    await reenter()
            @read_only_ingest('ozon')
            async def reenter():
                admit_ingest_read(settings.ozon_seller_base, 'POST', '/v3/product/list')
            @read_only_ingest('ozon')
            async def parent():
                task = asyncio.create_task(child())
                await entered.wait()
                return task
            task = await parent()
            release.set()
            await task
        with patch.object(settings, 'marketplace_read_only', True):
            asyncio.run(scenario())

    def test_legacy_and_outside_preserved(self):
        admit_ingest_read('unknown', 'DELETE', '/unknown')
        @read_only_ingest('ozon')
        async def op():
            admit_ingest_read('unknown', 'DELETE', '/unknown')
        with patch.object(settings, 'marketplace_read_only', False):
            asyncio.run(op())

    def test_parallel_and_cancellation(self):
        async def scenario():
            entered = asyncio.Event()
            @read_only_ingest('ozon')
            async def waiting():
                entered.set()
                await asyncio.Future()
            task = asyncio.create_task(waiting())
            await entered.wait()
            # A sibling task must not inherit the other task's guard.
            admit_ingest_read('unknown', 'DELETE', '/unknown')
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            admit_ingest_read('unknown', 'DELETE', '/unknown')
        with patch.object(settings, 'marketplace_read_only', True):
            asyncio.run(scenario())

    def test_exception_cleanup_and_stale_child(self):
        async def scenario():
            ready = asyncio.Event()
            async def child():
                await ready.wait()
                with self.assertRaises(ExecutionError):
                    admit_ingest_read(settings.ozon_seller_base, 'POST', '/v3/product/info/list')
            @read_only_ingest('ozon')
            async def parent():
                task = asyncio.create_task(child())
                return task
            task = await parent()
            ready.set()
            await task
            @read_only_ingest('ozon')
            async def broken():
                raise ValueError('synthetic')
            with self.assertRaises(ValueError):
                await broken()
            admit_ingest_read('unknown', 'DELETE', '/unknown')
        with patch.object(settings, 'marketplace_read_only', True):
            asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main(verbosity=2)
