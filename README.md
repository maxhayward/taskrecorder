# taskrecorder (`wl`)

A tiny work log CLI backed by SQLite. Python stdlib only.

```sh
uv tool install --editable .     # puts `wl` on your PATH
```

## Logging

```sh
wl add "Code review: first PR" 45m -t engineering   # assumes it just finished
wl add "Quarterly plan" 1h30m --at "yesterday 10:00"
wl start "Email triage"    # live timer (stops any running one)
wl status
wl stop
```

Durations: `45`, `45m`, `1.5h`, `1h30m`, `1:30`.
Dates: `today`, `yesterday`, `3d` (3 days ago), `mon`…`sun` (most recent), `2026-10-01`, optionally followed by `HH:MM`.

## Reporting

```sh
wl report                       # last 7 days, grouped by task
wl report --today | --yesterday | --week | --last-week | --month
wl report --since 2026-09-01 --until 2026-09-30
wl report --by day              # or --by tag
wl report -t engineering            # filter to one tag
```

## Web UI

```sh
wl serve              # opens http://localhost:8765 (--port, --no-open)
```

Add, edit and delete entries, start/stop the timer, and see a breakdown of where
time went over any range: totals, a stacked chart per day (or per week for long
ranges), and a ranked breakdown by tag or task. Click a breakdown row to filter
the entries list; click an entry to edit it.

The server is Python stdlib and only listens on localhost. The UI is plain JS with
no runtime dependencies; Vite is the only (dev) dependency, used to build it into
`taskrecorder/static/`. To work on it:

```sh
wl serve --no-open &          # API on :8765
cd web && yarn install && yarn dev    # hot-reloading UI on :5173, proxies /api
yarn build                 # rebuild taskrecorder/static for `wl serve`
```

## Fixing mistakes

```sh
wl ls                 # recent entries with ids
wl edit 12 --duration 30m --task "Better name" --tag ''
wl rm 12
```

Data lives in `~/.local/share/taskrecorder/worklog.db` (override with `WL_DB=/path/to.db`).
You can query it directly: `sqlite3 ~/.local/share/taskrecorder/worklog.db 'select * from entries'`.

## Uninstalling

```sh
uv tool uninstall taskrecorder    # removes the `wl` command
```

This leaves your data in place. To delete that too (irreversible — back it up first if you might want it):

```sh
rm -r ~/.local/share/taskrecorder
```
