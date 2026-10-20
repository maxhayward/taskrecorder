import { fmtMinutes, isoDate } from "./util.js";

const NS = "http://www.w3.org/2000/svg";
const H = 240;
const M = { top: 18, right: 8, bottom: 26, left: 38 };
const GAP = 2; // surface gap between stacked segments
const R = 4; // rounded data-end

function svg(tag, attrs = {}) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

/** A rect whose top corners are rounded (the data end); square at the base. */
function topRounded(x, y, w, h, r) {
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function niceStep(maxHours) {
  const raw = maxHours / 4;
  const steps = [0.25, 0.5, 1, 2, 3, 4, 5, 10, 20, 25, 50, 100];
  return steps.find((s) => s >= raw) ?? Math.ceil(raw / 100) * 100;
}

/** Stacked columns of time per bucket (day or week), one segment per series. */
export function renderChart(container, summary, { focus, tooltip }) {
  const { buckets, seriesOrder, seriesColor, weekly } = summary;
  const peak = Math.max(0, ...buckets.map((b) => b.total));
  if (!peak) {
    container.replaceChildren(Object.assign(document.createElement("p"), { className: "empty", textContent: "Nothing logged in this range." }));
    return;
  }

  const W = Math.max(container.clientWidth, buckets.length * 14 + M.left + M.right);
  const plotW = W - M.left - M.right;
  const plotH = H - M.top - M.bottom;
  const step = niceStep(peak / 60);
  const yMax = Math.ceil(peak / 60 / step) * step;
  const y = (minutes) => M.top + plotH - (minutes / 60 / yMax) * plotH;
  const band = plotW / buckets.length;
  const barW = Math.max(4, Math.min(24, band * 0.6));

  const root = svg("svg", { width: W, height: H, role: "img", "aria-label": `Stacked columns of time per ${weekly ? "week" : "day"}` });

  for (let v = 0; v <= yMax + 1e-9; v += step) {
    const yy = Math.round(y(v * 60)) + 0.5;
    root.append(svg("line", { x1: M.left, x2: W - M.right, y1: yy, y2: yy, class: v === 0 ? "base" : "grid" }));
    const label = svg("text", { x: M.left - 6, y: yy + 4, "text-anchor": "end" });
    label.textContent = `${+v.toFixed(2)}h`;
    root.append(label);
  }

  // Thin x labels so they never collide (~44px each).
  const every = Math.max(1, Math.ceil(44 / band));
  const today = isoDate(new Date());
  const peakBucket = buckets.reduce((a, b) => (b.total > a.total ? b : a));

  buckets.forEach((b, i) => {
    const cx = M.left + band * (i + 0.5);
    const x = cx - barW / 2;
    const g = svg("g");
    const layers = seriesOrder.filter((s) => b.values.get(s));
    let base = 0;
    layers.forEach((s, li) => {
      const m = b.values.get(s);
      const top = y(base + m);
      const bottom = y(base);
      base += m;
      const isTop = li === layers.length - 1;
      const h = bottom - top - (isTop || bottom - top <= GAP + 1 ? 0 : GAP);
      if (h <= 0) return;
      const shape = isTop
        ? svg("path", { d: topRounded(x, top, barW, bottom - top, R) })
        : svg("rect", { x, y: top + (bottom - top - h), width: barW, height: h });
      shape.setAttribute("fill", seriesColor(s));
      shape.setAttribute("class", `seg-rect${focus && focus !== s ? " dim" : ""}`);
      g.append(shape);
    });

    if (b === peakBucket) {
      const t = svg("text", { x: cx, y: y(b.total) - 5, "text-anchor": "middle" });
      t.textContent = fmtMinutes(b.total);
      g.append(t);
    }

    if (i % every === 0) {
      const t = svg("text", { x: cx, y: H - 8, "text-anchor": "middle" });
      const longDays = !weekly && buckets.length <= 14;
      t.textContent = weekly
        ? b.start.toLocaleDateString(undefined, { day: "numeric", month: "short" })
        : longDays
          ? b.start.toLocaleDateString(undefined, { weekday: "short", day: "numeric" })
          : String(b.start.getDate());
      if (!weekly && b.key === today) t.setAttribute("style", "fill: var(--ink); font-weight: 600");
      g.append(t);
    }

    // Hit target: the whole column band, much larger than the mark.
    const hit = svg("rect", { x: cx - band / 2, y: M.top, width: band, height: plotH, fill: "transparent" });
    hit.addEventListener("pointerenter", () => showTip(tooltip, b, layers, seriesColor, weekly));
    hit.addEventListener("pointermove", (ev) => placeTip(tooltip, ev));
    hit.addEventListener("pointerleave", () => (tooltip.hidden = true));
    g.append(hit);
    root.append(g);
  });

  container.replaceChildren(root);
}

function showTip(tip, bucket, layers, color, weekly) {
  const head = weekly
    ? `Week of ${bucket.start.toLocaleDateString(undefined, { day: "numeric", month: "short" })}`
    : bucket.start.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
  const rows = [...layers].reverse().map((s) => {
    const row = document.createElement("div");
    row.className = "t-row";
    const sw = document.createElement("i");
    sw.className = "swatch";
    sw.style.background = color(s);
    const v = document.createElement("b");
    v.textContent = fmtMinutes(bucket.values.get(s));
    row.append(sw, s, v);
    return row;
  });
  const h = document.createElement("div");
  h.className = "t-head";
  h.textContent = `${head} · ${bucket.total ? fmtMinutes(bucket.total) : "nothing logged"}`;
  tip.replaceChildren(h, ...rows);
  tip.hidden = false;
}

function placeTip(tip, ev) {
  const pad = 14;
  const { width, height } = tip.getBoundingClientRect();
  let left = ev.clientX + pad;
  let top = ev.clientY + pad;
  if (left + width > innerWidth - 8) left = ev.clientX - width - pad;
  if (top + height > innerHeight - 8) top = ev.clientY - height - pad;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, top)}px`;
}
