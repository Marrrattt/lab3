"""Integration tests as the restricted application user, rolled back per test."""
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
import getpass
import io
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from database import Store, TABLES, InputError, connect
from main import dispatch, main
from reporting import configure_logging, logger, run_action


class StoreTests(unittest.TestCase):
    username = "bookstore_user"
    password = ""

    def setUp(self):
        self.connection = connect(self.username, self.password)
        self.transaction = self.connection.transaction(force_rollback=True)
        self.transaction.__enter__()
        self.store = Store(self.connection)
        self.output = io.StringIO()
        self.redirect = redirect_stdout(self.output)
        self.redirect.__enter__()

    def tearDown(self):
        try:
            self.transaction.__exit__(None, None, None)
        finally:
            self.connection.close()
            self.redirect.__exit__(None, None, None)

    def test_select_all_five_tables(self):
        for table in TABLES:
            self.assertTrue(self.store.select(table), table)

    def test_single_and_multiple_filters(self):
        self.assertEqual(len(self.store.select("books", {"id": 1})), 1)
        rows = self.store.select("books", {"author_id": 1, "stock": 10})
        self.assertEqual(rows[0]["id"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.store.select("customers", {"phone": None})[0]["id"], 2)

    def test_update_any_non_id_fields(self):
        rows = self.store.update_one("books", 1, {"price": 812.25, "stock": 22})
        self.assertEqual(rows[0]["price"], Decimal("812.25"))
        self.assertEqual(rows[0]["stock"], 22)
        self.assertEqual(self.store.update_one("books", 999999, {"stock": 3}), [])

    def test_update_many_in(self):
        rows = self.store.update_many("books", "id", [1, 2], {"price": 650})
        self.assertEqual({row["id"] for row in rows}, {1, 2})
        self.assertTrue(all(row["price"] == 650 for row in rows))
        self.assertEqual(self.store.select("books", {"id": 3})[0]["price"], 600)

    def test_insert_one_and_many(self):
        row = self.store.insert_one("authors", {"name": "Один", "country": "Россия"})
        rows = self.store.insert_many("authors", [{"name": "Два", "country": "Франция"}, {"name": "Три", "country": "Россия"}])
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.store.select("authors", row)[0]["name"], "Один")

    def test_returning_linked_insert(self):
        ids = self.store.insert_linked({"name": "Связанный", "country": "Россия"},
            {"title": "Книга", "isbn": str(uuid4()), "price": 500})
        book = self.store.select("books", {"id": ids["book_id"]})[0]
        self.assertEqual(book["author_id"], ids["author_id"])

    def test_advanced_batch_links(self):
        ids = self.store.insert_order_batch({"name": "Тест", "email": str(uuid4())}, [
            {"status": "new", "items": [{"book_id": 1, "quantity": 2, "unit_price": 750}, {"book_id": 2, "quantity": 1, "unit_price": 450}]},
            {"status": "paid", "items": [{"book_id": 3, "quantity": 1, "unit_price": 600}]},
        ])
        self.assertEqual(len(ids["orders"]), 2)
        self.assertEqual(sum(len(order["item_ids"]) for order in ids["orders"]), 3)
        self.assertEqual(len(self.store.select("orders", {"customer_id": ids["customer_id"]})), 2)

    def test_bulk_rollback(self):
        before = len(self.store.select("authors"))
        with self.assertRaises(psycopg.errors.NotNullViolation):
            self.store.insert_many("authors", [{"name": "Будет отменён", "country": "Россия"}, {"country": "Россия"}])
        self.assertEqual(len(self.store.select("authors")), before)

    def test_linked_rollback(self):
        before = len(self.store.select("authors"))
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.store.insert_linked({"name": "Отмена", "country": "Россия"},
                {"title": "Тест", "isbn": str(uuid4()), "price": -1})
        self.assertEqual(len(self.store.select("authors")), before)

    def test_advanced_rollback_after_first_order(self):
        before = {table: len(self.store.select(table)) for table in ("customers", "orders", "order_items")}
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.store.insert_order_batch({"name": "Отмена", "email": str(uuid4())}, [
                {"status": "new", "items": [{"book_id": 1, "quantity": 1, "unit_price": 750}]},
                {"status": "new", "items": [{"book_id": 999999, "quantity": 1, "unit_price": 1}]},
            ])
        for table, count in before.items():
            self.assertEqual(len(self.store.select(table)), count)

    def test_identifiers_and_ids_rejected(self):
        for action in [
            lambda: self.store.select('books; DROP TABLE authors; --'),
            lambda: self.store.select('books', {'id OR 1=1': 1}),
            lambda: self.store.update_one('books', 1, {'id': 7}),
            lambda: self.store.update_one('books', 1, {'price=0; --': 1}),
            lambda: self.store.update_many('books', 'id) OR TRUE --', [1], {'price': 1}),
            lambda: self.store.update_many('books', 'id', [], {'price': 1}),
            lambda: self.store.insert_one('authors', {'name': 'x', 'country': 'y', 'id': 1}),
        ]:
            with self.assertRaises(InputError):
                action()

    def test_values_and_groups_are_not_sql(self):
        attack = "x'); DROP TABLE authors; --"
        row = self.store.insert_one("authors", {"name": attack, "country": "Тест"})
        self.assertEqual(self.store.select("authors", {"name": attack})[0]["id"], row["id"])
        self.assertEqual(self.store.select("authors", {"name": "' OR 1=1 --"}), [])
        self.assertEqual(self.store.update_many("authors", "name", ["' OR 1=1 --"], {"country": "x"}), [])
        self.assertTrue(self.store.select("books"))

    def test_least_privilege_enforced_by_server(self):
        for query in ["UPDATE books SET id=DEFAULT WHERE id=-1", "DELETE FROM authors WHERE id=-1", "CREATE TABLE public.forbidden(id int)"]:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with self.connection.transaction():
                    self.connection.execute(query)
        row = self.connection.execute("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user").fetchone()
        self.assertFalse(any(row.values()))

    def test_constraint_error_friendly_console_raw_file(self):
        with tempfile.TemporaryDirectory() as folder:
            logfile = Path(folder) / "raw.log"
            old_handlers = list(logger.handlers)
            try:
                with patch.dict('os.environ', {'APP_LOG_FILE': str(logfile)}):
                    configure_logging()
                err = io.StringIO()
                with redirect_stderr(err):
                    ok, _ = run_action('duplicate email', lambda: self.store.insert_one("customers", {"name":"X", "email":"anna@example.test"}))
                self.assertFalse(ok)
                self.assertIn("уже существует", err.getvalue())
                self.assertNotIn("Traceback", err.getvalue())
                self.assertNotIn("UniqueViolation", err.getvalue())
                self.assertIn("UniqueViolation", logfile.read_text(encoding="utf-8"))
                self.assertTrue(self.store.select("books"))
            finally:
                for handler in list(logger.handlers):
                    if handler not in old_handlers:
                        logger.removeHandler(handler)
                        handler.close()

    def test_menu_dispatch(self):
        with patch('builtins.input', side_effect=['books', '{"id":1}']):
            self.assertEqual(dispatch(self.store, '2')[0]['id'], 1)
        with patch('builtins.input', side_effect=['books', '{bad json']):
            with self.assertRaises(InputError):
                dispatch(self.store, '2')

    def test_authentication_and_connection_injection(self):
        for username, password in [(self.username, 'wrong-password'), (self.username + ' host=evil.invalid', self.password)]:
            with self.assertRaises(psycopg.OperationalError):
                connect(username, password)

    def test_cli_connect_select_exit(self):
        with patch.dict('os.environ', {'APP_LOG_FILE': ''}), patch('builtins.input', side_effect=[self.username, '1', 'books', '0']), patch('getpass.getpass', return_value=self.password):
            self.assertEqual(main(), 0)
        self.assertIn('подключение к базе данных', self.output.getvalue())
        self.assertIn('Мастер и Маргарита', self.output.getvalue())

    def test_web_dispatch_reuses_transactional_store(self):
        from web import dispatch_operation
        before = len(self.store.select('authors'))
        result = dispatch_operation(self.store, {'action':'insert_linked',
            'author':{'name':'Web test','country':'Test'},
            'book':{'title':'Web book','isbn':str(uuid4()),'price':100}})
        rows = dispatch_operation(self.store, {'action':'select','table':'books','filters':{'id':result['book_id']}})
        self.assertEqual(rows[0]['author_id'], result['author_id'])
        with self.assertRaises(InputError):
            dispatch_operation(self.store, {'action':'update_one','table':'books','id':1,'changes':{'id':7}})
        self.assertEqual(len(self.store.select('authors')), before+1)


if __name__ == '__main__':
    if sys.stdin.isatty():
        StoreTests.password = getpass.getpass('Пароль bookstore_user для тестов: ')
    else:
        StoreTests.password = json.load(sys.stdin)['password']
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StoreTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
