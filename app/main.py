import getpass
import json
import sys

from database import InputError, Store, TABLES, columns_for, connect
from reporting import configure_logging, logger, run_action, success
from interaction import ask, OperationCancelled

MENU = """
1 — Просмотреть таблицу без фильтра
2 — Фильтр по одной колонке
3 — Фильтр по нескольким колонкам (AND)
4 — Изменить одну запись по id
5 — Изменить несколько записей (IN)
6 — Добавить одну строку
7 — Добавить автора и его книгу (RETURNING id)
8 — Добавить несколько строк в одну таблицу
9 — Новый покупатель, несколько заказов и их позиции
0 — Выход
Во время операции: /back или «назад» — отменить и вернуться в меню; Ctrl+C — тоже отмена.
"""


def read_json(prompt):
    try:
        return json.loads(ask(prompt))
    except (ValueError, RecursionError):
        raise InputError('Некорректный JSON. Строки и названия полей заключайте в двойные кавычки.') from None


def choose_table():
    print("Таблицы: " + ", ".join(TABLES))
    table = ask("Таблица (/back — в меню): ").strip()
    print("Колонки: " + ", ".join(columns_for(table)))
    return table


def display(result):
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def dispatch(store, choice):
    if choice in {"1", "2", "3", "4", "5", "6", "8"}:
        table = choose_table()
    if choice == "1":
        return store.select(table)
    if choice in {"2", "3"}:
        print('Пример: {"id": 1} или {"author_id": 1, "stock": 10}')
        filters = read_json("Фильтр JSON: ")
        minimum = 1 if choice == "2" else 2
        if not isinstance(filters, dict) or len(filters) < minimum or (choice == "2" and len(filters) != 1):
            raise InputError("Для пункта 2 нужна одна колонка; для пункта 3 — минимум две.")
        return store.select(table, filters)
    if choice == "4":
        try:
            row_id = int(ask("Id записи: "))
        except ValueError:
            raise InputError("Введите целочисленный id.") from None
        print('Пример изменений: {"price": 800, "stock": 20}. Id изменять нельзя.')
        return store.update_one(table, row_id, read_json("Изменения JSON: "))
    if choice == "5":
        column = ask("Колонка фильтра IN: ").strip()
        values = read_json('Значения JSON-массивом, например [1, 2]: ')
        changes = read_json('Общие изменения, например {"price": 650}: ')
        return store.update_many(table, column, values, changes)
    if choice == "6":
        print('Id создаётся автоматически. Пример для authors: {"name": "Новый автор", "country": "Россия"}')
        return store.insert_one(table, read_json("Строка JSON: "))
    if choice == "7":
        author = read_json('Автор, например {"name": "Новый автор", "country": "Россия"}: ')
        book = read_json('Книга без id и author_id, например {"title": "Новая книга", "isbn": "demo-101", "price": 500, "stock": 5}: ')
        return store.insert_linked(author, book)
    if choice == "8":
        print('Пример для authors: [{"name":"Автор А","country":"Россия"},{"name":"Автор Б","country":"Франция"}]')
        return store.insert_many(table, read_json("Массив строк JSON: "))
    if choice == "9":
        customer = read_json('Покупатель, например {"name":"Мария","email":"maria@example.test"}: ')
        print('Пример: [{"status":"new","items":[{"book_id":1,"quantity":2,"unit_price":750}]},{"status":"paid","items":[{"book_id":3,"quantity":1,"unit_price":600}]}]')
        return store.insert_order_batch(customer, read_json("Заказы JSON: "))
    raise InputError("Выберите пункт меню от 0 до 9.")


def main():
    try:
        configure_logging()
    except (OSError, ValueError):
        print("Не удалось открыть журнал. Проверьте APP_LOG_FILE и права на папку.", file=sys.stderr)
        return 1
    try:
        print("Книжный магазин — лабораторная №3")
        username = input("Логин PostgreSQL: ")
        password = getpass.getpass("Пароль (ввод скрыт): ")
        ok, connection = run_action("подключение к базе данных", lambda: connect(username, password))
        del password
        if not ok:
            return 1
        with connection:
            store = Store(connection)
            while True:
                print(MENU)
                choice = input("Пункт: ").strip()
                if choice == "0":
                    break
                try:
                    ok, result = run_action("операция с данными завершена", lambda: dispatch(store, choice))
                except (OperationCancelled, KeyboardInterrupt):
                    success("Операция отменена. Возврат в главное меню.")
                    continue
                if ok:
                    display(result)
                    if result == []:
                        print("Подходящие записи не найдены.")
                if connection.closed:
                    print("Соединение закрыто. Запустите приложение заново.", file=sys.stderr)
                    return 1
        success("Соединение закрыто.")
        return 0
    except (EOFError, KeyboardInterrupt):
        success("Ввод отменён. Работа завершена.")
        return 0
    except Exception:
        logger.exception("Unexpected application error")
        print("Работа прервана. Проверьте настройки и повторите запуск.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
