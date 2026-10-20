"""wl - a tiny command-line work log backed by SQLite."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_DB = Path.home() / ".local" / "share" / "taskrecorder" / "worklog.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task       TEXT    NOT NULL,
    tag        TEXT,
    started_at TEXT    NOT NULL,  -- ISO-8601 local time
    minutes    INTEGER            -- NULL while a timer is running
);
CREATE INDEX IF NOT EXISTS idx_entries_started_at ON entries(started_at);
"""


# --------------------------------------------------------------------------- db


def connect() -> sqlite3.Connection:
    path = Path(os.environ.get("WL_DB", DEFAULT_DB)).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------------- parsing

_DURATION_RE = re.compile(r"^(?:(\d+(?:\.\d+)?)h)?\s*(?:(\d+)m)?$")


def parse_duration(text: str) -> int:
    """Parse '90', '90m', '1.5h', '1h30m', '1:30' into whole minutes."""
    s = text.strip().lower().replace(" ", "")
    if re.fullmatch(r"\d+", s):
        return int(s)
    if m := re.fullmatch(r"(\d+):(\d{1,2})", s):
        return int(m[1]) * 60 + int(m[2])
    m = _DURATION_RE.fullmatch(s)
    if not s or not m or not (m[1] or m[2]):
        raise argparse.ArgumentTypeError(
            f"can't parse duration {text!r} (try 45m, 1.5h, 1h30m or 1:30)"
        )
    return round(float(m[1] or 0) * 60) + int(m[2] or 0)


def parse_day(text: str) -> date:
    """Parse 'today', 'yesterday', '3d' (3 days ago), 'mon'..'sun' or YYYY-MM-DD."""
    s = text.strip().lower()
    today = date.today()
    if s == "today":
        return today
    if s == "yesterday":
        return today - timedelta(days=1)
    if m := re.fullmatch(r"(\d+)d", s):
        return today - timedelta(days=int(m[1]))
    weekdays = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    if s[:3] in weekdays and len(s) >= 3:
        back = (today.weekday() - weekdays.index(s[:3])) % 7
        return today - timedelta(days=back)
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"can't parse date {text!r} (try today, yesterday, 3d, mon, 2026-10-01)"
        ) from None


def parse_when(text: str) -> datetime:
    """Parse a point in time: '14:30', 'yesterday 14:30', '2026-10-01 09:00', or a day."""
    parts = text.strip().split()
    day, clock = date.today(), None
    for p in parts:
        if re.fullmatch(r"\d{1,2}:\d{2}", p):
            h, mi = map(int, p.split(":"))
            clock = (h, mi)
        else:
            day = parse_day(p)
    h, mi = clock if clock else (9, 0)
    return datetime(day.year, day.month, day.day, h, mi)


def fmt_minutes(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


# --------------------------------------------------------------------- commands


def running_entry(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM entries WHERE minutes IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()


def cmd_add(args: argparse.Namespace) -> None:
    conn = connect()
    if args.at:
        started = parse_when(args.at)
    else:
        # Default: assume the task just finished, so it started `duration` ago.
        started = now() - timedelta(minutes=args.duration)
    with conn:
        cur = conn.execute(
            "INSERT INTO entries (task, tag, started_at, minutes) VALUES (?, ?, ?, ?)",
            (args.task, args.tag, started.isoformat(timespec="minutes"), args.duration),
        )
    print(
        f"#{cur.lastrowid}  {fmt_minutes(args.duration)}  {args.task}"
        + (f"  [{args.tag}]" if args.tag else "")
        + f"  (started {started:%a %d %b %H:%M})"
    )


def _stop(conn: sqlite3.Connection, entry: sqlite3.Row) -> int:
    started = datetime.fromisoformat(entry["started_at"])
    minutes = max(1, round((datetime.now() - started).total_seconds() / 60))
    with conn:
        conn.execute("UPDATE entries SET minutes = ? WHERE id = ?", (minutes, entry["id"]))
    print(f"stopped #{entry['id']}  {fmt_minutes(minutes)}  {entry['task']}")
    return minutes


def cmd_start(args: argparse.Namespace) -> None:
    conn = connect()
    if current := running_entry(conn):
        _stop(conn, current)
    started = now()
    with conn:
        cur = conn.execute(
            "INSERT INTO entries (task, tag, started_at, minutes) VALUES (?, ?, ?, NULL)",
            (args.task, args.tag, started.isoformat(timespec="minutes")),
        )
    print(f"started #{cur.lastrowid}  {args.task} at {started:%H:%M}")


def cmd_stop(args: argparse.Namespace) -> None:
    conn = connect()
    if current := running_entry(conn):
        _stop(conn, current)
    else:
        print("nothing running")


def cmd_status(args: argparse.Namespace) -> None:
    conn = connect()
    if current := running_entry(conn):
        started = datetime.fromisoformat(current["started_at"])
        elapsed = round((datetime.now() - started).total_seconds() / 60)
        print(f"running #{current['id']}  {current['task']}  for {fmt_minutes(elapsed)}")
    else:
        print("nothing running")


def resolve_range(args: argparse.Namespace) -> tuple[date, date]:
    today = date.today()
    if args.today:
        return today, today
    if args.yesterday:
        y = today - timedelta(days=1)
        return y, y
    if args.week:
        return today - timedelta(days=today.weekday()), today
    if args.last_week:
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6)
    if args.month:
        return today.replace(day=1), today
    since = args.since or (today - timedelta(days=6))
    until = args.until or today
    return since, until


def fetch(conn: sqlite3.Connection, since: date, until: date, tag: str | None):
    sql = (
        "SELECT * FROM entries WHERE started_at >= ? AND started_at < ? "
        "AND minutes IS NOT NULL"
    )
    params: list = [since.isoformat(), (until + timedelta(days=1)).isoformat()]
    if tag:
        sql += " AND tag = ?"
        params.append(tag)
    return conn.execute(sql + " ORDER BY started_at", params).fetchall()


def bar(minutes: int, peak: int, width: int = 24) -> str:
    return "█" * max(1, round(width * minutes / peak)) if peak else ""


def cmd_report(args: argparse.Namespace) -> None:
    conn = connect()
    since, until = resolve_range(args)
    rows = fetch(conn, since, until, args.tag)

    header = f"{since:%a %d %b %Y}" + (f" → {until:%a %d %b %Y}" if until != since else "")
    print(header + (f"  [tag: {args.tag}]" if args.tag else ""))
    print("─" * len(header))
    if not rows:
        print("no entries")
        return

    total = sum(r["minutes"] for r in rows)

    if args.by == "day":
        groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for r in rows:
            groups[r["started_at"][:10]].append(r)
        for day_str, items in groups.items():
            day_total = sum(r["minutes"] for r in items)
            print(f"\n{date.fromisoformat(day_str):%a %d %b}  {fmt_minutes(day_total)}")
            per_task: dict[str, int] = defaultdict(int)
            for r in items:
                per_task[r["task"]] += r["minutes"]
            for task, mins in sorted(per_task.items(), key=lambda kv: -kv[1]):
                print(f"  {fmt_minutes(mins):>7}  {task}")
    else:
        key = "tag" if args.by == "tag" else "task"
        totals: dict[str, int] = defaultdict(int)
        for r in rows:
            totals[r[key] or "(untagged)"] += r["minutes"]
        ranked = sorted(totals.items(), key=lambda kv: -kv[1])
        width = min(max(len(k) for k, _ in ranked), 50)
        peak = ranked[0][1]
        for name, mins in ranked:
            pct = 100 * mins / total
            label = name if len(name) <= width else name[: width - 1] + "…"
            print(f"{label:<{width}}  {fmt_minutes(mins):>7}  {pct:4.0f}%  {bar(mins, peak)}")

    print(f"\ntotal: {fmt_minutes(total)} across {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}")


def cmd_list(args: argparse.Namespace) -> None:
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM entries ORDER BY started_at DESC, id DESC LIMIT ?", (args.n,)
    ).fetchall()
    if not rows:
        print("no entries yet — try: wl add \"write report\" 45m")
        return
    for r in reversed(rows):
        started = datetime.fromisoformat(r["started_at"])
        dur = fmt_minutes(r["minutes"]) if r["minutes"] is not None else "running"
        tag = f"  [{r['tag']}]" if r["tag"] else ""
        print(f"#{r['id']:<4} {started:%a %d %b %H:%M}  {dur:>7}  {r['task']}{tag}")


def cmd_delete(args: argparse.Namespace) -> None:
    conn = connect()
    with conn:
        cur = conn.execute("DELETE FROM entries WHERE id = ?", (args.id,))
    print(f"deleted #{args.id}" if cur.rowcount else f"no entry #{args.id}")


def cmd_edit(args: argparse.Namespace) -> None:
    conn = connect()
    updates: dict[str, object] = {}
    if args.task:
        updates["task"] = args.task
    if args.duration is not None:
        updates["minutes"] = args.duration
    if args.tag is not None:
        updates["tag"] = args.tag or None
    if args.at:
        updates["started_at"] = parse_when(args.at).isoformat(timespec="minutes")
    if not updates:
        sys.exit("nothing to change (use --task, --duration, --tag or --at)")
    sets = ", ".join(f"{k} = ?" for k in updates)
    with conn:
        cur = conn.execute(
            f"UPDATE entries SET {sets} WHERE id = ?", [*updates.values(), args.id]
        )
    print(f"updated #{args.id}" if cur.rowcount else f"no entry #{args.id}")


def cmd_serve(args: argparse.Namespace) -> None:
    from .server import serve

    serve(args.port, open_browser=not args.no_open)


# ------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wl",
        description="Record what you've been working on and report on it.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="log a task you've done", aliases=["a"])
    a.add_argument("task", help="what you worked on")
    a.add_argument("duration", type=parse_duration, help="e.g. 45m, 1.5h, 1h30m, 1:30")
    a.add_argument("-t", "--tag", help="optional tag/project, e.g. 'engineering'")
    a.add_argument(
        "--at",
        help="when it started, e.g. '14:00', 'yesterday 10:30', '2026-10-01' "
        "(default: it just finished)",
    )
    a.set_defaults(func=cmd_add)

    s = sub.add_parser("start", help="start a live timer (stops any running one)")
    s.add_argument("task")
    s.add_argument("-t", "--tag")
    s.set_defaults(func=cmd_start)

    sub.add_parser("stop", help="stop the running timer").set_defaults(func=cmd_stop)
    sub.add_parser("status", help="show the running timer").set_defaults(func=cmd_status)

    r = sub.add_parser("report", help="breakdown over a period", aliases=["r"])
    rng = r.add_mutually_exclusive_group()
    rng.add_argument("--today", action="store_true")
    rng.add_argument("--yesterday", action="store_true")
    rng.add_argument("--week", action="store_true", help="this week (Mon → today)")
    rng.add_argument("--last-week", action="store_true")
    rng.add_argument("--month", action="store_true", help="this month so far")
    r.add_argument("--since", type=parse_day, help="start day (default: 6 days ago)")
    r.add_argument("--until", type=parse_day, help="end day, inclusive (default: today)")
    r.add_argument("--by", choices=["task", "day", "tag"], default="task")
    r.add_argument("-t", "--tag", help="only include this tag")
    r.set_defaults(func=cmd_report)

    ls = sub.add_parser("list", help="show recent entries with their ids", aliases=["ls"])
    ls.add_argument("-n", type=int, default=20, help="how many (default 20)")
    ls.set_defaults(func=cmd_list)

    e = sub.add_parser("edit", help="change an entry")
    e.add_argument("id", type=int)
    e.add_argument("--task")
    e.add_argument("--duration", type=parse_duration)
    e.add_argument("--tag", help="pass '' to clear")
    e.add_argument("--at")
    e.set_defaults(func=cmd_edit)

    d = sub.add_parser("delete", help="remove an entry", aliases=["rm"])
    d.add_argument("id", type=int)
    d.set_defaults(func=cmd_delete)

    sv = sub.add_parser("serve", help="open the web UI")
    sv.add_argument("-p", "--port", type=int, default=8765, help="default 8765")
    sv.add_argument("--no-open", action="store_true", help="don't open a browser")
    sv.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
