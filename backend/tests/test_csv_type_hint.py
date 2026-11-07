"""Header evidence owns the type; a UI choice is only an ambiguity fallback."""
import unittest

from tasks.csv_parser import parse_csv


class ImportTypeHintTests(unittest.TestCase):
    def test_finance_headers_win_over_products_hint(self):
        result = parse_csv('Дата,Артикул WB,Выручка,Чистая прибыль\n06.11.2026,ART-1,100,85\n'.encode(), 'wb', 'products', import_type_is_hint=True)
        self.assertEqual(result.import_type, 'finance')
        self.assertEqual(result.parsed_data[0]['revenue'], 100)

    def test_product_headers_win_over_finance_hint(self):
        result = parse_csv('Артикул WB,Цена,Остаток\nART-1,100,3\n'.encode(), 'wb', 'finance', import_type_is_hint=True)
        self.assertEqual(result.import_type, 'products')
        self.assertEqual(result.parsed_data[0]['stock'], 3)

    def test_returns_headers_win_over_products_hint(self):
        result = parse_csv('Дата,Артикул WB,Количество возвратов,Причина возврата\n06.11.2026,ART-1,2,Брак\n'.encode(), 'wb', 'products', import_type_is_hint=True)
        self.assertEqual(result.import_type, 'returns')

    def test_content_headers_win_over_products_hint(self):
        result = parse_csv('Артикул WB,Описание,Характеристики\nART-1,Учебное описание,Учебная характеристика\n'.encode(), 'wb', 'products', import_type_is_hint=True)
        self.assertEqual(result.import_type, 'card_content')

    def test_ambiguous_headers_keep_explicit_hint(self):
        result = parse_csv(b'sku\nART-1\n', 'wb', 'products')
        self.assertEqual(result.import_type, 'products')
        self.assertEqual(result.valid_rows, 1)

    def test_ambiguous_headers_without_hint_stay_blocked(self):
        result = parse_csv(b'sku\nART-1\n', 'wb')
        self.assertIsNone(result.import_type)
        self.assertTrue(result.errors)

    def test_authoritative_marketplace_is_not_replaced(self):
        result = parse_csv('Артикул Ozon,Выручка\nART-1,100\n'.encode(), 'wb', 'products')
        self.assertEqual(result.marketplace, 'wb')

    def test_tied_content_and_product_scores_keep_matching_hint(self):
        result = parse_csv('sku,Описание,Остаток\nART-1,Описание товара,4\n'.encode(), 'wb', 'card_content', import_type_is_hint=True)
        self.assertEqual(result.import_type, 'card_content')
        self.assertEqual(result.parsed_data[0]['description'], 'Описание товара')

    def test_tied_return_and_finance_scores_keep_matching_hint(self):
        result = parse_csv('sku,Дата возврата,Количество,Комиссия\nART-1,06.11.2026,2,10\n'.encode(), 'wb', 'returns', import_type_is_hint=True)
        self.assertEqual(result.import_type, 'returns')

    def test_confirmation_default_preserves_frozen_type_even_with_conflicting_headers(self):
        result = parse_csv('sku,Описание,Остаток\nART-1,Описание товара,4\n'.encode(), 'wb', 'card_content')
        self.assertEqual(result.import_type, 'card_content')
        self.assertEqual(result.parsed_data[0]['description'], 'Описание товара')


if __name__ == '__main__':
    unittest.main()
