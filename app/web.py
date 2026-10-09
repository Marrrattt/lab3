"""Local web UI with server-side sessions; credentials are not retained."""
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
import sys

import psycopg
from database import Store, TABLES, InputError, connect
from reporting import configure_logging, friendly_error, logger, success

SESSION_TTL = 1800
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()
STATIC = {"/": ("index.html", "text/html"), "/ui.js": ("ui.js", "application/javascript"), "/style.css": ("style.css", "text/css")}


def expire_sessions():
    with SESSIONS_LOCK:
        expired = [SESSIONS.pop(key) for key in list(SESSIONS)
                   if time.monotonic() - SESSIONS[key]["touched"] > SESSION_TTL]
    for session in expired:
        with session["lock"]:
            session["connection"].close()


def dispatch_operation(store, data):
    action = data.get("action")
    table = data.get("table")
    if action == "select":
        return store.select(table, data.get("filters", {}))
    if action == "update_one":
        return store.update_one(table, data.get("id"), data.get("changes"))
    if action == "update_many":
        return store.update_many(table, data.get("column"), data.get("values"), data.get("changes"))
    if action == "insert_one":
        return store.insert_one(table, data.get("data"))
    if action == "insert_many":
        return store.insert_many(table, data.get("rows"))
    if action == "insert_linked":
        return store.insert_linked(data.get("author"), data.get("book"))
    if action == "insert_order_batch":
        return store.insert_order_batch(data.get("customer"), data.get("orders"))
    raise InputError("Неизвестная операция.")


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def respond(self, status, payload, *, cookie=None, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        for key, value in {
            "Content-Type": content_type + "; charset=utf-8", "Content-Length": str(len(body)),
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self'",
        }.items():
            self.send_header(key, value)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def session_token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            return cookie["bookstore_session"].value if "bookstore_session" in cookie else ""
        except Exception:
            return ""

    def get_session(self):
        expire_sessions()
        with SESSIONS_LOCK:
            session = SESSIONS.get(self.session_token())
            if session:
                session["touched"] = time.monotonic()
            return session

    def do_GET(self):
        if self.path in STATIC:
            filename, content_type = STATIC[self.path]
            self.respond(200, Path(__file__).with_name(filename).read_bytes(), content_type=content_type)
        elif self.path == "/api/session":
            session = self.get_session()
            if not session:
                self.respond(401, {"error": "Войдите в базу данных."})
            else:
                self.respond(200, {"username": session["username"], "csrf": session["csrf"], "tables": TABLES})
        else:
            self.respond(404, {"error": "Страница не найдена."})

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            self.respond(403, {"error": "Откройте приложение с его локального адреса."})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond(415, {"error": "Ожидается JSON."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, OSError):
            self.respond(400, {"error": "Некорректный запрос. Проверьте введённые данные."})
            return
        if self.path == "/api/login":
            self.login(data)
            return
        session = self.get_session()
        if not session:
            self.respond(401, {"error": "Сеанс завершён. Войдите снова."})
            return
        if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
            self.respond(403, {"error": "Обновите страницу и повторите операцию."})
            return
        if self.path == "/api/logout":
            with SESSIONS_LOCK:
                SESSIONS.pop(self.session_token(), None)
            with session["lock"]:
                session["connection"].close()
            self.respond(200, {"ok": True}, cookie="bookstore_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            return
        if self.path != "/api/operation":
            self.respond(404, {"error": "Операция не найдена."})
            return
        try:
            with session["lock"]:
                result = dispatch_operation(Store(session["connection"]), data)
            success("Веб-операция с данными завершена.")
            self.respond(200, {"result": result})
        except Exception as error:
            logger.exception("Web operation failed")
            message = friendly_error(error)
            print("Ошибка: " + message, file=sys.stderr, flush=True)
            self.respond(400, {"error": message})

    def login(self, data):
        if set(data) != {"username", "password"} or any(not isinstance(v, str) for v in data.values()):
            self.respond(400, {"error": "Введите логин и пароль."})
            return
        try:
            connection = connect(data["username"], data["password"])
        except Exception as error:
            logger.exception("Web database login failed")
            print("Ошибка подключения к базе данных.", file=sys.stderr, flush=True)
            self.respond(401, {"error": friendly_error(error)})
            return
        expire_sessions()
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with SESSIONS_LOCK:
            old = SESSIONS.pop(self.session_token(), None)
            SESSIONS[token] = {"connection": connection, "username": data["username"], "csrf": csrf,
                               "touched": time.monotonic(), "lock": threading.Lock()}
        if old:
            with old["lock"]:
                old["connection"].close()
        success("Веб-подключение к базе данных успешно.")
        self.respond(200, {"username": data["username"], "csrf": csrf, "tables": TABLES},
                     cookie=f"bookstore_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}")

    def log_message(self, format, *args):
        print("HTTP " + format % args, file=sys.stderr, flush=True)


if __name__ == "__main__":
    configure_logging()
    def cleanup():
        while True:
            time.sleep(60)
            expire_sessions()
    threading.Thread(target=cleanup, daemon=True).start()
    print("Книжный магазин: http://localhost:8083", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
