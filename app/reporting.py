"""Friendly console messages; optional technical file log."""
import logging
import os
from pathlib import Path
import sys

import psycopg
from database import InputError
from interaction import OperationCancelled

logger = logging.getLogger("bookstore")
logger.addHandler(logging.NullHandler())
logger.propagate = False


def configure_logging():
    logger.setLevel(logging.DEBUG)
    path = os.environ.get("APP_LOG_FILE")
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(target, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)


def success(message):
    print(message, flush=True)
    logger.info(message)


def friendly_error(error):
    if isinstance(error, InputError):
        return str(error)
    if isinstance(error, psycopg.errors.UniqueViolation):
        return "Запись с таким уникальным значением уже существует."
    if isinstance(error, psycopg.errors.ForeignKeyViolation):
        return "Связанная запись не найдена. Проверьте идентификаторы."
    if isinstance(error, psycopg.errors.NotNullViolation):
        return "Заполните все обязательные поля."
    if isinstance(error, psycopg.errors.CheckViolation):
        return "Значение не соответствует ограничениям: проверьте цену, количество и статус."
    if isinstance(error, psycopg.errors.InsufficientPrivilege):
        return "У пользователя нет прав для этой операции."
    if isinstance(error, psycopg.DataError):
        return "Неверный тип или формат значения. Проверьте числа и даты."
    if isinstance(error, psycopg.OperationalError):
        return "Нет соединения с базой. Проверьте запуск сервера, настройки, логин и пароль."
    return "Не удалось выполнить операцию. Проверьте введённые данные и настройки."


def run_action(label, action):
    try:
        result = action()
    except OperationCancelled:
        raise
    except Exception as error:
        # Technical details and traceback go only to the optional file.
        logger.exception("FAILED %s", label)
        print(f"Ошибка: {friendly_error(error)}", file=sys.stderr, flush=True)
        return False, None
    success(f"Успешно: {label}.")
    return True, result
