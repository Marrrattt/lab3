"""SQL: identifiers are allowlisted and quoted; all values are parameters."""
import json
import logging
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

TABLES = {
    "authors": ("id", "name", "country"),
    "books": ("id", "author_id", "title", "isbn", "price", "stock"),
    "customers": ("id", "name", "email", "phone"),
    "orders": ("id", "customer_id", "status", "created_at"),
    "order_items": ("id", "order_id", "book_id", "quantity", "unit_price"),
}


class InputError(ValueError):
    """A user-facing validation error with no database internals."""


def load_config():
    config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or set(config) != {"host", "port", "dbname", "connect_timeout"}:
        raise InputError("Некорректный набор полей файла настроек.")
    for key in ("host", "dbname"):
        if not isinstance(config[key], str) or not config[key] or "\0" in config[key]:
            raise InputError("Некорректное текстовое поле настроек.")
    for key, limit in (("port", 65535), ("connect_timeout", 60)):
        if type(config[key]) is not int or not 1 <= config[key] <= limit:
            raise InputError("Некорректное числовое поле настроек.")
    return config


def connect(username, password):
    if not username or not password or "\0" in username or "\0" in password:
        raise InputError("Логин и пароль должны быть непустыми и не содержать NUL.")
    return psycopg.connect(**load_config(), user=username, password=password,
                           autocommit=True, row_factory=dict_row)


def columns_for(table):
    if not isinstance(table, str) or table not in TABLES:
        raise InputError("Выберите таблицу из списка.")
    return TABLES[table]


def validate_fields(table, data, *, writable=False, allow_empty=False):
    allowed = columns_for(table)
    if not isinstance(data, dict) or (not data and not allow_empty):
        raise InputError("Введите непустой JSON-объект с названиями колонок.")
    if any(key not in allowed or (writable and key == "id") for key in data):
        raise InputError("Недопустимая колонка. Поле id нельзя задавать или изменять.")
    if any(isinstance(value, (dict, list)) for value in data.values()):
        raise InputError("Значения полей должны быть числами, строками или null.")


def nonempty_list(value):
    if not isinstance(value, list) or not value:
        raise InputError("Введите непустой JSON-массив.")


class Store:
    def __init__(self, connection):
        self.connection = connection

    def _execute(self, cursor, query, values):
        # Log SQL templates without credential or value substitution.
        logger = logging.getLogger("bookstore")
        template = query.as_string(self.connection)
        try:
            cursor.execute(query, values)
        except Exception:
            logger.exception("SQL FAILED: %s", template)
            raise
        logger.info("SQL OK: %s", template)
        print("Запрос выполнен." if template.startswith("SELECT") else
              "Запрос выполнен в транзакции; сохранение после завершения операции.")

    def select(self, table, filters=None):
        filters = {} if filters is None else filters
        validate_fields(table, filters, allow_empty=True)
        query = sql.SQL("SELECT * FROM {}").format(sql.Identifier("public", table))
        conditions, values = [], []
        for column, value in filters.items():
            if value is None:
                conditions.append(sql.SQL("{} IS NULL").format(sql.Identifier(column)))
            else:
                conditions.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
                values.append(value)
        if conditions:
            query += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
        query += sql.SQL(" ORDER BY id")
        with self.connection.cursor() as cursor:
            self._execute(cursor, query, values)
            return cursor.fetchall()

    def update_one(self, table, row_id, changes):
        validate_fields(table, changes, writable=True)
        if type(row_id) is not int or row_id < 1:
            raise InputError("Id должен быть положительным целым числом.")
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(column)) for column in changes)
        query = sql.SQL("UPDATE {} SET {} WHERE id = %s RETURNING *").format(
            sql.Identifier("public", table), assignments)
        with self.connection.transaction(), self.connection.cursor() as cursor:
            self._execute(cursor, query, [*changes.values(), row_id])
            return cursor.fetchall()

    def update_many(self, table, column, values, changes):
        validate_fields(table, changes, writable=True)
        if not isinstance(column, str) or column not in columns_for(table):
            raise InputError("Выберите колонку фильтра из списка.")
        nonempty_list(values)
        if any(value is None or isinstance(value, (dict, list)) for value in values):
            raise InputError("Для IN нужны отдельные непустые значения, без null и вложенных массивов.")
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(key)) for key in changes)
        # One placeholder for each IN value; never join raw user strings.
        placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in values)
        query = sql.SQL("UPDATE {} SET {} WHERE {} IN ({}) RETURNING *").format(
            sql.Identifier("public", table), assignments, sql.Identifier(column), placeholders)
        with self.connection.transaction(), self.connection.cursor() as cursor:
            self._execute(cursor, query, [*changes.values(), *values])
            return cursor.fetchall()

    def _insert(self, table, data):
        validate_fields(table, data, writable=True)
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
            sql.Identifier("public", table),
            sql.SQL(", ").join(sql.Identifier(key) for key in data),
            sql.SQL(", ").join(sql.Placeholder() for _ in data))
        with self.connection.cursor() as cursor:
            self._execute(cursor, query, list(data.values()))
            return cursor.fetchone()["id"]

    def insert_one(self, table, data):
        with self.connection.transaction():
            return {"id": self._insert(table, data)}

    def insert_many(self, table, rows):
        nonempty_list(rows)
        with self.connection.transaction():
            return [{"id": self._insert(table, row)} for row in rows]

    def insert_linked(self, author, book):
        """Insert author, use RETURNING id for the new book's foreign key."""
        validate_fields("authors", author, writable=True)
        validate_fields("books", book, writable=True)
        if "author_id" in book:
            raise InputError("author_id будет получен автоматически из новой записи автора.")
        with self.connection.transaction():
            author_id = self._insert("authors", author)
            book_id = self._insert("books", {**book, "author_id": author_id})
            return {"author_id": author_id, "book_id": book_id}

    def insert_order_batch(self, customer, orders):
        """One new customer, multiple new orders and items, all or nothing."""
        nonempty_list(orders)
        with self.connection.transaction():
            customer_id = self._insert("customers", customer)
            result = []
            for order in orders:
                if not isinstance(order, dict) or set(order) != {"status", "items"}:
                    raise InputError("Каждый заказ должен содержать status и items.")
                nonempty_list(order["items"])
                order_id = self._insert("orders", {"customer_id": customer_id, "status": order["status"]})
                item_ids = []
                for item in order["items"]:
                    if not isinstance(item, dict) or set(item) != {"book_id", "quantity", "unit_price"}:
                        raise InputError("Позиция должна содержать book_id, quantity, unit_price.")
                    item_ids.append(self._insert("order_items", {**item, "order_id": order_id}))
                result.append({"order_id": order_id, "item_ids": item_ids})
            return {"customer_id": customer_id, "orders": result}
