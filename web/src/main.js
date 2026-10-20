import { renderChart } from "./chart.js";
import { $, el, fmtMinutes, isoDate, parseDate, addDays, api } from "./util.js";

const SLOTS = 7; // colored series; anything beyond folds into "Other"
const OTHER = "Other";
const UNTAGGED = "(untagged)";

const state = {
  preset: "week",
  since: null,
  until: null,
  groupBy: "tag",
  focus: null, // a group key the entries list is filtered to
  editing: null, // entry being edited
  entries: [],
  meta: { tasks: [], tags: [], running: null },
};

// ------------------------------------------------------------------ ranges

function presetRange(preset, anchor = new Date()) {
  const d = new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate());
  switch (preset) {
    case "today":
      return [d, d];
    case "week": {
      const mon = addDays(d, -((d.getDay() + 6) % 7));
      return [mon, addDays(mon, 6)];
    }
    case "month":
      return [new Date(d.getFullYear(), d.getMonth(), 1), new Date(d.getFullYear(), d.getMonth() + 1, 0)];
    case "30d":
      return [addDays(d, -29), d];
  }
}

function shiftRange(dir) {
  const since = parseDate(state.since);
  const until = parseDate(state.until);
  if (state.preset === "month") {
    [state.since, state.until] = presetRange("month", new Date(since.getFullYear(), since.getMonth() + dir, 1)).map(isoDate);
  } else {
    const len = Math.round((until - since) / 864e5) + 1;
    state.since = isoDate(addDays(since, dir * len));
    state.until = isoDate(addDays(until, dir * len));
  }
}

function rangeLabel() {
  const s = parseDate(state.since);
  const u = parseDate(state.until);
  const opts = { weekday: "short", day: "numeric", month: "short" };
  if (state.since === state.until) return s.toLocaleDateString(undefined, { ...opts, year: "numeric" });
  if (state.preset === "month") return s.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  return `${s.toLocaleDateString(undefined, opts)} – ${u.toLocaleDateString(undefined, { ...opts, year: "numeric" })}`;
}

function saveHash() {
  const p = new URLSearchParams({ range: state.preset, since: state.since, until: state.until, by: state.groupBy });
  history.replaceState(null, "", `#${p}`);
}

function loadHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("by") === "task") state.groupBy = "task";
  const preset = p.get("range");
  if (preset && p.get("since") && p.get("until")) {
    state.preset = preset;
    state.since = p.get("since");
    state.until = p.get("until");
  } else {
    [state.since, state.until] = presetRange(state.preset).map(isoDate);
  }
}

// --------------------------------------------------------------- grouping

const groupKey = (e) => (state.groupBy === "tag" ? e.tag || UNTAGGED : e.task);

/** Map each group to a color. Tags keep a stable slot (by all-time use) so colors
 *  don't shuffle as the range changes; tasks are ranked within the range. */
function colorMap(totals) {
  const colors = new Map();
  const ordered =
    state.groupBy === "tag"
      ? state.meta.tags.filter((t) => totals.has(t))
      : [...totals.keys()].filter((k) => k !== UNTAGGED).sort((a, b) => totals.get(b) - totals.get(a));
  ordered.slice(0, SLOTS).forEach((k, i) => colors.set(k, `var(--s${i + 1})`));
  return {
    series: (k) => (colors.has(k) || k === UNTAGGED ? k : OTHER),
    color: (k) => colors.get(k) ?? (k === UNTAGGED ? "var(--untagged)" : "var(--other)"),
  };
}

function summarize() {
  const done = state.entries.filter((e) => e.minutes != null);
  const totals = new Map();
  for (const e of done) totals.set(groupKey(e), (totals.get(groupKey(e)) ?? 0) + e.minutes);
  const { series, color } = colorMap(totals);

  // Series for the chart: colored groups + Other + untagged, in display order.
  const seriesTotals = new Map();
  for (const [k, m] of totals) seriesTotals.set(series(k), (seriesTotals.get(series(k)) ?? 0) + m);
  const seriesOrder = [...seriesTotals.keys()].sort((a, b) => {
    const rank = (s) => (s === OTHER ? 2 : s === UNTAGGED ? 1 : 0);
    return rank(a) - rank(b) || seriesTotals.get(b) - seriesTotals.get(a);
  });
  const seriesColor = (s) => (s === OTHER ? "var(--other)" : s === UNTAGGED ? "var(--untagged)" : color(s));

  // Buckets: days, or ISO weeks once the range gets long.
  const since = parseDate(state.since);
  const until = parseDate(state.until);
  const days = Math.round((until - since) / 864e5) + 1;
  const weekly = days > 45;
  const buckets = [];
  const index = new Map();
  for (let d = since; d <= until; d = addDays(d, 1)) {
    const start = weekly ? addDays(d, -((d.getDay() + 6) % 7)) : d;
    const key = isoDate(start);
    if (!index.has(key)) {
      index.set(key, buckets.length);
      buckets.push({ key, start, values: new Map(), total: 0 });
    }
    index.set(isoDate(d), index.get(key));
  }
  for (const e of done) {
    const b = buckets[index.get(e.started_at.slice(0, 10))];
    if (!b) continue;
    const s = series(groupKey(e));
    b.values.set(s, (b.values.get(s) ?? 0) + e.minutes);
    b.total += e.minutes;
  }

  const total = done.reduce((n, e) => n + e.minutes, 0);
  const activeDays = new Set(done.map((e) => e.started_at.slice(0, 10))).size;
  const ranked = [...totals.entries()].sort((a, b) => b[1] - a[1]);
  return { done, total, activeDays, ranked, color, series, buckets, weekly, seriesOrder, seriesColor };
}

// ---------------------------------------------------------------- rendering

function render() {
  for (const b of $("#presets").children) b.setAttribute("aria-pressed", b.dataset.preset === state.preset);
  for (const b of $("#groupby").children) b.setAttribute("aria-pressed", b.dataset.group === state.groupBy);
  $("#range-label").textContent = rangeLabel();
  $("#custom").hidden = state.preset !== "custom";
  $("#since").value = state.since;
  $("#until").value = state.until;

  const s = (state.summary = summarize());
  renderKpis(s);
  $("#chart-title").textContent = s.weekly ? "Time per week" : "Time per day";
  $("#legend").replaceChildren(
    ...(s.seriesOrder.length > 1
      ? s.seriesOrder.map((k) => el("span", {}, el("i", { class: "swatch", style: `background:${s.seriesColor(k)}` }), k))
      : []),
  );
  drawChart();
  renderBreakdown(s);
  renderEntries(s);
  renderTimer();
}

function renderKpis({ total, activeDays, ranked, done }) {
  const top = ranked[0];
  const kpi = (label, value, sub = "") =>
    el("div", { class: "kpi" }, el("div", { class: "label" }, label), el("div", { class: "value", title: value }, value), el("div", { class: "sub" }, sub));
  $("#kpis").replaceChildren(
    kpi("Total", fmtMinutes(total), `${done.length} entr${done.length === 1 ? "y" : "ies"}`),
    kpi("Per active day", activeDays ? fmtMinutes(Math.round(total / activeDays)) : "—", `${activeDays} day${activeDays === 1 ? "" : "s"} logged`),
    kpi(state.groupBy === "tag" ? "Top tag" : "Top task", top ? top[0] : "—", top ? `${fmtMinutes(top[1])} · ${Math.round((100 * top[1]) / total)}%` : ""),
  );
}

function renderBreakdown({ ranked, total, color }) {
  const peak = ranked[0]?.[1] ?? 0;
  if (!ranked.length) {
    $("#breakdown").replaceChildren(el("li", { class: "empty" }, "Nothing logged in this range."));
    return;
  }
  $("#breakdown").replaceChildren(
    ...ranked.map(([k, m]) => {
      const li = el(
        "li",
        { class: state.focus === k ? "active" : "", tabindex: 0, role: "button", "aria-pressed": state.focus === k },
        el("span", { class: "name", title: k }, el("i", { class: "swatch", style: `background:${color(k)}` }), el("span", { class: "text" }, k)),
        el("div", { class: "track" }, el("div", { class: "fill", style: `width:${(100 * m) / peak}%;background:${color(k)}` })),
        el("span", { class: "num" }, fmtMinutes(m)),
        el("span", { class: "pct" }, `${Math.round((100 * m) / total)}%`),
      );
      const toggle = () => {
        state.focus = state.focus === k ? null : k;
        render();
      };
      li.addEventListener("click", toggle);
      li.addEventListener("keydown", (ev) => (ev.key === "Enter" || ev.key === " ") && (ev.preventDefault(), toggle()));
      return li;
    }),
  );
}

function renderEntries({ color }) {
  const focus = $("#focus");
  focus.hidden = !state.focus;
  if (state.focus) {
    const clear = el("button", { type: "button", "aria-label": "Clear filter" }, "×");
    clear.addEventListener("click", () => ((state.focus = null), render()));
    focus.replaceChildren(state.focus, clear);
  }

  const list = state.entries.filter((e) => !state.focus || groupKey(e) === state.focus);
  if (!list.length) {
    $("#entries").replaceChildren(el("p", { class: "empty" }, "No entries."));
    return;
  }
  const byDay = new Map();
  for (const e of [...list].reverse()) {
    const d = e.started_at.slice(0, 10);
    if (!byDay.has(d)) byDay.set(d, []);
    byDay.get(d).push(e);
  }
  $("#entries").replaceChildren(
    ...[...byDay].map(([day, items]) => {
      const total = items.reduce((n, e) => n + (e.minutes ?? 0), 0);
      const dayLabel = parseDate(day).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
      return el(
        "div",
        { class: "day" },
        el("div", { class: "day-head" }, el("span", {}, dayLabel), el("span", {}, fmtMinutes(total))),
        ...items.map((e) => {
          const tagKey = e.tag || UNTAGGED;
          const btn = el(
            "button",
            { type: "button", class: `entry${state.editing?.id === e.id ? " selected" : ""}`, title: `Edit #${e.id}` },
            el("span", { class: "time" }, e.started_at.slice(11, 16)),
            el(
              "span",
              { class: "what" },
              el("span", { class: "task" }, e.task),
              e.tag
                ? el("span", { class: "tag" }, el("i", { class: "swatch", style: `background:${state.groupBy === "tag" ? color(tagKey) : "var(--axis)"}` }), e.tag)
                : "",
            ),
            e.minutes == null ? el("span", { class: "dur running" }, "running") : el("span", { class: "dur" }, fmtMinutes(e.minutes)),
          );
          btn.addEventListener("click", () => editEntry(e));
          return btn;
        }),
      );
    }),
  );
}

function renderTimer() {
  const r = state.meta.running;
  $("#timer").hidden = !r;
  if (!r) return;
  const elapsed = Math.max(0, Math.round((Date.now() - new Date(r.started_at)) / 6e4));
  $("#timer-label").textContent = `${r.task}${r.tag ? ` [${r.tag}]` : ""} · ${fmtMinutes(elapsed)}`;
}

function drawChart() {
  const s = state.summary;
  if (s) renderChart($("#chart"), s, { focus: state.focus && s.series(state.focus), tooltip: $("#tooltip") });
}

// ------------------------------------------------------------------- form

const form = $("#form");

function resetForm() {
  state.editing = null;
  form.reset();
  form.date.value = isoDate(new Date());
  form.classList.remove("editing");
  $("#form-title").textContent = "Add entry";
  $("#save").textContent = "Add";
  $("#form-hint").hidden = false;
  $("#start-timer").hidden = false;
  $("#cancel").hidden = true;
  resetDelete(true);
  showError("");
}

function editEntry(e) {
  state.editing = e;
  form.task.value = e.task;
  form.tag.value = e.tag ?? "";
  form.duration.value = e.minutes == null ? "" : fmtMinutes(e.minutes);
  form.duration.placeholder = e.minutes == null ? "running" : "45m, 1h30m, 1:30";
  form.date.value = e.started_at.slice(0, 10);
  form.time.value = e.started_at.slice(11, 16);
  form.classList.add("editing");
  $("#form-title").textContent = `Edit #${e.id}`;
  $("#save").textContent = "Save";
  $("#form-hint").hidden = true;
  $("#start-timer").hidden = true;
  $("#cancel").hidden = false;
  resetDelete(false);
  showError("");
  render();
  form.scrollIntoView({ block: "nearest", behavior: "smooth" });
  form.task.focus();
}

function resetDelete(hidden) {
  const del = $("#delete");
  del.hidden = hidden;
  del.classList.remove("confirming");
  del.textContent = "Delete";
}

function showError(msg) {
  $("#form-error").hidden = !msg;
  $("#form-error").textContent = msg;
}

function formBody() {
  const body = { task: form.task.value, tag: form.tag.value };
  const duration = form.duration.value.trim();
  if (duration) body.duration = duration;
  const date = form.date.value;
  const time = form.time.value;
  if (state.editing) {
    body.started_at = `${date}T${time || state.editing.started_at.slice(11, 16)}`;
  } else if (time || date !== isoDate(new Date())) {
    // Matches the CLI: a past day with no time given starts at 09:00.
    body.started_at = `${date}T${time || "09:00"}`;
  }
  return body;
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = formBody();
  if (!state.editing && !body.duration) return showError("Add a duration, or use Start timer.");
  try {
    if (state.editing) await api(`/api/entries/${state.editing.id}`, "PATCH", body);
    else await api("/api/entries", "POST", body);
    const keepDate = form.date.value;
    resetForm();
    form.date.value = keepDate;
    await refresh();
  } catch (err) {
    showError(err.message);
  }
});

$("#start-timer").addEventListener("click", async () => {
  try {
    await api("/api/timer/start", "POST", { task: form.task.value, tag: form.tag.value });
    resetForm();
    await refresh();
  } catch (err) {
    showError(err.message);
  }
});

$("#cancel").addEventListener("click", () => (resetForm(), render()));

$("#delete").addEventListener("click", async () => {
  const del = $("#delete");
  if (!del.classList.contains("confirming")) {
    del.classList.add("confirming");
    del.textContent = "Really delete?";
    return;
  }
  try {
    await api(`/api/entries/${state.editing.id}`, "DELETE");
    resetForm();
    await refresh();
  } catch (err) {
    showError(err.message);
  }
});

$("#timer-stop").addEventListener("click", async () => {
  await api("/api/timer/stop", "POST", {});
  await refresh();
});

// ---------------------------------------------------------------- toolbar

$("#presets").addEventListener("click", (ev) => {
  const preset = ev.target.closest("button")?.dataset.preset;
  if (!preset) return;
  state.preset = preset;
  if (preset !== "custom") [state.since, state.until] = presetRange(preset).map(isoDate);
  refresh();
});
$("#prev").addEventListener("click", () => (shiftRange(-1), refresh()));
$("#next").addEventListener("click", () => (shiftRange(1), refresh()));
for (const id of ["since", "until"]) {
  $(`#${id}`).addEventListener("change", (ev) => {
    if (!ev.target.value) return;
    state[id] = ev.target.value;
    if (state.since > state.until) [state.since, state.until] = [state.until, state.since];
    refresh();
  });
}
$("#groupby").addEventListener("click", (ev) => {
  const group = ev.target.closest("button")?.dataset.group;
  if (!group || group === state.groupBy) return;
  state.groupBy = group;
  state.focus = null;
  saveHash();
  render();
});

// ------------------------------------------------------------------- data

async function refresh() {
  saveHash();
  const [list, meta] = await Promise.all([
    api(`/api/entries?since=${state.since}&until=${state.until}`),
    api("/api/meta"),
  ]);
  state.entries = list.entries;
  state.meta = meta;
  $("#task-list").replaceChildren(...meta.tasks.map((t) => el("option", { value: t })));
  $("#tag-list").replaceChildren(...meta.tags.map((t) => el("option", { value: t })));
  if (state.focus && !state.entries.some((e) => groupKey(e) === state.focus)) state.focus = null;
  render();
}

loadHash();
resetForm();
refresh().catch((err) => {
  $("#entries").replaceChildren(el("p", { class: "error" }, `Couldn't reach the server: ${err.message}`));
});
new ResizeObserver(drawChart).observe($("#chart"));
setInterval(renderTimer, 30_000);
