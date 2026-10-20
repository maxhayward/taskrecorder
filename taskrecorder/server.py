"""`wl serve` - a small JSON API over the work log, plus the built web UI."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sqlite3
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .cli import connect, now, parse_duration

STATIC_DIR = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


class ApiError(Exception):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def row_to_dict(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in r.keys()}


def parse_started_at(text: str) -> str:
    try:
        return datetime.fromisoformat(text).isoformat(timespec="minutes")
    except (TypeError, ValueError):
        raise ApiError(f"can't parse start time {text!r}") from None


def parse_minutes(body: dict) -> int:
    raw = body.get("duration")
    try:
        minutes = raw if isinstance(raw, int) else parse_duration(str(raw or ""))
    except argparse.ArgumentTypeError as e:
        raise ApiError(str(e)) from None
    if minutes <= 0:
        raise ApiError("duration must be more than zero")
    return minutes


def clean_task(body: dict) -> str:
    task = str(body.get("task") or "").strip()
    if not task:
        raise ApiError("task can't be empty")
    return task


def clean_tag(body: dict) -> str | None:
    return str(body.get("tag") or "").strip() or None


# ------------------------------------------------------------------- handlers


def list_entries(conn: sqlite3.Connection, query: dict) -> dict:
    today = date.today()
    try:
        since = date.fromisoformat(query.get("since", [""])[0] or (today - timedelta(days=6)).isoformat())
        until = date.fromisoformat(query.get("until", [""])[0] or today.isoformat())
    except ValueError:
        raise ApiError("since/until must be YYYY-MM-DD") from None
    rows = conn.execute(
        "SELECT * FROM entries WHERE started_at >= ? AND started_at < ? "
        "ORDER BY started_at, id",
        (since.isoformat(), (until + timedelta(days=1)).isoformat()),
    ).fetchall()
    return {"since": since.isoformat(), "until": until.isoformat(), "entries": [row_to_dict(r) for r in rows]}


def meta(conn: sqlite3.Connection) -> dict:
    """Known tasks/tags for autocomplete, plus any running timer."""
    tasks = [r[0] for r in conn.execute(
        "SELECT task FROM entries GROUP BY task ORDER BY MAX(started_at) DESC LIMIT 200"
    )]
    tags = [r[0] for r in conn.execute(
        "SELECT tag FROM entries WHERE tag IS NOT NULL GROUP BY tag ORDER BY COUNT(*) DESC"
    )]
    running = conn.execute(
        "SELECT * FROM entries WHERE minutes IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {"tasks": tasks, "tags": tags, "running": row_to_dict(running) if running else None}


def create_entry(conn: sqlite3.Connection, body: dict) -> dict:
    task, tag, minutes = clean_task(body), clean_tag(body), parse_minutes(body)
    if body.get("started_at"):
        started = parse_started_at(body["started_at"])
    else:
        started = (now() - timedelta(minutes=minutes)).isoformat(timespec="minutes")
    with conn:
        cur = conn.execute(
            "INSERT INTO entries (task, tag, started_at, minutes) VALUES (?, ?, ?, ?)",
            (task, tag, started, minutes),
        )
    return get_entry(conn, cur.lastrowid)


def update_entry(conn: sqlite3.Connection, entry_id: int, body: dict) -> dict:
    get_entry(conn, entry_id)  # 404 if missing
    updates: dict[str, object] = {}
    if "task" in body:
        updates["task"] = clean_task(body)
    if "tag" in body:
        updates["tag"] = clean_tag(body)
    if "duration" in body:
        updates["minutes"] = parse_minutes(body)
    if "started_at" in body:
        updates["started_at"] = parse_started_at(body["started_at"])
    if updates:
        sets = ", ".join(f"{k} = ?" for k in updates)
        with conn:
            conn.execute(f"UPDATE entries SET {sets} WHERE id = ?", [*updates.values(), entry_id])
    return get_entry(conn, entry_id)


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> dict:
    with conn:
        cur = conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    if not cur.rowcount:
        raise ApiError(f"no entry #{entry_id}", HTTPStatus.NOT_FOUND)
    return {"deleted": entry_id}


def start_timer(conn: sqlite3.Connection, body: dict) -> dict:
    task, tag = clean_task(body), clean_tag(body)
    stop_timer(conn)
    with conn:
        cur = conn.execute(
            "INSERT INTO entries (task, tag, started_at, minutes) VALUES (?, ?, ?, NULL)",
            (task, tag, now().isoformat(timespec="minutes")),
        )
    return get_entry(conn, cur.lastrowid)


def stop_timer(conn: sqlite3.Connection, body: dict | None = None) -> dict:
    running = conn.execute("SELECT * FROM entries WHERE minutes IS NULL").fetchall()
    for r in running:
        started = datetime.fromisoformat(r["started_at"])
        minutes = max(1, round((datetime.now() - started).total_seconds() / 60))
        with conn:
            conn.execute("UPDATE entries SET minutes = ? WHERE id = ?", (minutes, r["id"]))
    return {"stopped": [r["id"] for r in running]}


def get_entry(conn: sqlite3.Connection, entry_id: int) -> dict:
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if not row:
        raise ApiError(f"no entry #{entry_id}", HTTPStatus.NOT_FOUND)
    return row_to_dict(row)


ENTRY_RE = re.compile(r"^/api/entries/(\d+)$")


class Handler(BaseHTTPRequestHandler):
    server_version = "wl"

    def log_request(self, code="-", size="-"):  # keep the terminal quiet except for errors
        if isinstance(code, int) and code >= 400:
            super().log_request(code, size)

    # -- plumbing

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _host_ok(self) -> bool:
        # Guard against DNS rebinding: only answer to requests addressed to localhost.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ALLOWED_HOSTS

    def _body(self) -> dict:
        # Requiring a JSON content type means a cross-site <form> can't post here.
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ApiError("expected application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            raise ApiError("invalid JSON") from None
        if not isinstance(body, dict):
            raise ApiError("expected a JSON object")
        return body

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
            return
        url = urlsplit(self.path)
        if not url.path.startswith("/api/"):
            if method == "GET":
                self._static(url.path)
            else:
                self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})
            return
        conn = connect()
        try:
            self._json(HTTPStatus.OK, self._route(conn, method, url.path, parse_qs(url.query)))
        except ApiError as e:
            self._json(e.status, {"error": str(e)})
        finally:
            conn.close()

    def _route(self, conn, method: str, path: str, query: dict) -> object:
        if path == "/api/entries" and method == "GET":
            return list_entries(conn, query)
        if path == "/api/entries" and method == "POST":
            return create_entry(conn, self._body())
        if m := ENTRY_RE.match(path):
            entry_id = int(m[1])
            if method == "PATCH":
                return update_entry(conn, entry_id, self._body())
            if method == "DELETE":
                return delete_entry(conn, entry_id)
        if path == "/api/meta" and method == "GET":
            return meta(conn)
        if path == "/api/timer/start" and method == "POST":
            return start_timer(conn, self._body())
        if path == "/api/timer/stop" and method == "POST":
            self._body()
            return stop_timer(conn)
        raise ApiError("not found", HTTPStatus.NOT_FOUND)

    def _static(self, path: str) -> None:
        if not (STATIC_DIR / "index.html").exists():
            self._send(
                HTTPStatus.NOT_FOUND,
                b"UI not built. Run: cd web && npm install && npm run build\n",
                "text/plain; charset=utf-8",
            )
            return
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            target = STATIC_DIR / "index.html"
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


def serve(port: int, open_browser: bool) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}/"
    print(f"wl UI on {url}  (ctrl-c to stop)")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
