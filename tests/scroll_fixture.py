"""Local-only UI regression fixture. No bot, database, credentials or real users.

Run: python tests/scroll_fixture.py; open http://127.0.0.1:8002.
The page uses the production HTML/CSS/JS and a long synthetic conversation.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

STATIC = Path(__file__).resolve().parents[1] / "static"
CHAT = {"id": 1, "telegram_chat_id": -1, "user_name": "Prueba de scroll",
        "origin": "panel", "mode": "ai", "mood": "interesado", "profile": {},
        "summary": "Conversación ficticia para verificar desplazamiento.", "courses": [],
        "updated_at": "2026-10-29T18:30:00+00:00", "last_message": "Mensaje de prueba 80"}
MESSAGES = [{"id": i, "role": "user" if i % 2 else "assistant",
             "content": f"Mensaje de prueba {i}. " + "Información del curso y sus requisitos. " * 3,
             "created_at": "2026-10-29T18:30:00+00:00"} for i in range(1, 81)]


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        routes = {
            "/api/conversations": [CHAT],
            "/api/conversations/1": {**CHAT, "messages": MESSAGES},
            "/api/metrics": {"conversations": 1, "with_ai": 1, "with_human": 0, "moods": {}, "courses": 0},
            "/api/health": {"telegram": True, "telegram_status": "connected", "llm": True, "protected": True, "model": "Fixture local sin llamadas externas"},
            "/api/catalog": [],
        }
        assets = {"/": "index.html", "/static/styles.css": "styles.css", "/static/app.js": "app.js"}
        if path in assets:
            body = (STATIC / assets[path]).read_bytes()
            content_type = {"html": "text/html", "css": "text/css", "js": "application/javascript"}[assets[path].split(".")[-1]]
        elif path in routes:
            body, content_type = json.dumps(routes[path]).encode(), "application/json"
        elif path == "/api/events":
            self.send_response(204)
            self.end_headers()
            return
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8002), Fixture).serve_forever()
