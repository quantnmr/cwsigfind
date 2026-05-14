// CWSigFind Lite — browser-only spot aggregator.
//
// app.js is the thin orchestrator: it wires up the chips, search box,
// beacons widget, and help drawer; everything domain-specific lives in
// ./lib/. Polling runs as four independent loops (POTA / SOTA / WWFF /
// BOTA) — see ./lib/sources.js.

import { BAND_NAMES } from "./lib/spot.js";
import { currentBeacons } from "./lib/beacons.js";
import {
  getLastUpdated,
  loadIotaCatalog,
  startAllPollers,
} from "./lib/sources.js";
import { startPropagationPoller } from "./lib/propagation.js";

// ---------------------------------------------------------------------------
// Persisted UI prefs. Anything keyed under cwsigfind:* in localStorage.
// ---------------------------------------------------------------------------

const LS_PREFIX = "cwsigfind:";
const LSK = {
  bannerDismissed: LS_PREFIX + "banner-dismissed",
  maxVisible: LS_PREFIX + "max-visible",
  sources: LS_PREFIX + "sources",
  bands: LS_PREFIX + "bands",
  beaconsCollapsed: LS_PREFIX + "beacons-collapsed",
  propagationCollapsed: LS_PREFIX + "propagation-collapsed",
};

function lsGet(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v;
  } catch (e) { return fallback; }
}
function lsSet(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
}

// ---------------------------------------------------------------------------
// State.
// ---------------------------------------------------------------------------

const SOURCES = ["POTA", "SOTA", "WWFF", "BOTA"];
// Live state: rebuilt on every poll. We keep at most MAX_KEEP in memory so a
// long-lived tab doesn't grow unboundedly.
const allSpots = [];
const MAX_KEEP = 1000;

const enabledSources = new Set(
  (lsGet(LSK.sources, SOURCES.join(",")) || SOURCES.join(","))
    .split(",")
    .filter((s) => SOURCES.includes(s))
);
if (enabledSources.size === 0) for (const s of SOURCES) enabledSources.add(s);

const enabledBands = new Set(
  (lsGet(LSK.bands, "") || "")
    .split(",")
    .filter((b) => BAND_NAMES.includes(b))
);

let searchTerm = "";
let maxVisible = (() => {
  const v = lsGet(LSK.maxVisible, "100");
  return v === "all" ? Infinity : (parseInt(v, 10) || 100);
})();

// ---------------------------------------------------------------------------
// DOM refs.
// ---------------------------------------------------------------------------

const rowsEl = document.getElementById("rows");
const countEl = document.getElementById("count");
const searchEl = document.getElementById("search");
const bandChipsEl = document.getElementById("bandChips");
const maxSpotsSel = document.getElementById("maxSpotsSel");
const liteBanner = document.getElementById("liteBanner");
const sourceStatusEl = document.getElementById("sourceStatus");

// ---------------------------------------------------------------------------
// Banner (dismissible on first load).
// ---------------------------------------------------------------------------

if (lsGet(LSK.bannerDismissed, "0") !== "1") liteBanner.hidden = false;
document.getElementById("dismissBanner").onclick = () => {
  liteBanner.hidden = true;
  lsSet(LSK.bannerDismissed, "1");
};

// ---------------------------------------------------------------------------
// UTC clock — pinned next to the title so users don't have to mentally
// convert local time. All spot/beacon/propagation timestamps in this app
// are UTC, so the header clock matches the rest of the data. Tick once a
// second; the browser throttles to once-a-minute when the tab is hidden,
// which is fine.
// ---------------------------------------------------------------------------

const utcClockEl = document.getElementById("utcClock");
function tickUtcClock() {
  const d = new Date();
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  // The element's first child is the bare HH:MM:SS text node; the second
  // child is the "<span class='label'>UTC</span>" suffix. Mutating just the
  // text node avoids reflow of the styled label every tick.
  utcClockEl.firstChild.nodeValue = `${hh}:${mm}:${ss}`;
}
tickUtcClock();
setInterval(tickUtcClock, 1000);

// ---------------------------------------------------------------------------
// Band chips — generated from BAND_NAMES so they match the spot.band field.
// ---------------------------------------------------------------------------

for (const b of BAND_NAMES) {
  const c = document.createElement("button");
  c.className = "chip";
  c.dataset.band = b;
  c.textContent = b;
  if (enabledBands.has(b)) c.classList.add("active");
  c.onclick = () => {
    if (enabledBands.has(b)) { enabledBands.delete(b); c.classList.remove("active"); }
    else { enabledBands.add(b); c.classList.add("active"); }
    lsSet(LSK.bands, [...enabledBands].join(","));
    rerender();
  };
  bandChipsEl.appendChild(c);
}

// ---------------------------------------------------------------------------
// Source chips — same shape as the full app, minus DX / RBN / IOTA (no
// data in the lite build), plus an ALL convenience chip.
// ---------------------------------------------------------------------------

const sourceChipsEl = document.getElementById("sourceChips");
function refreshSourceChipState() {
  for (const chip of sourceChipsEl.querySelectorAll(".chip")) {
    const s = chip.dataset.src;
    if (s === "ALL") {
      const allActive = SOURCES.every((x) => enabledSources.has(x));
      chip.classList.toggle("active", allActive);
    } else {
      chip.classList.toggle("active", enabledSources.has(s));
    }
  }
}
refreshSourceChipState();

sourceChipsEl.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  const s = chip.dataset.src;
  if (s === "ALL") {
    const allActive = SOURCES.every((x) => enabledSources.has(x));
    enabledSources.clear();
    if (!allActive) for (const x of SOURCES) enabledSources.add(x);
    // If "all" was already on, treat the click as a clear-all reset.
    // (User can then click individual chips to build a custom selection.)
  } else {
    if (enabledSources.has(s)) enabledSources.delete(s);
    else enabledSources.add(s);
  }
  refreshSourceChipState();
  lsSet(LSK.sources, [...enabledSources].join(","));
  rerender();
});

// ---------------------------------------------------------------------------
// Search + max-spots dropdown.
// ---------------------------------------------------------------------------

searchEl.oninput = () => { searchTerm = searchEl.value.trim().toLowerCase(); rerender(); };

(() => {
  // Sync the dropdown's selected value with the persisted pref.
  const v = isFinite(maxVisible) ? String(maxVisible) : "all";
  if ([...maxSpotsSel.options].some((o) => o.value === v)) maxSpotsSel.value = v;
})();
maxSpotsSel.onchange = () => {
  maxVisible = maxSpotsSel.value === "all" ? Infinity : parseInt(maxSpotsSel.value, 10);
  lsSet(LSK.maxVisible, maxSpotsSel.value);
  rerender();
};

// ---------------------------------------------------------------------------
// Spot rendering.
// ---------------------------------------------------------------------------

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTime(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function matches(s) {
  if (!enabledSources.has(s.source)) return false;
  if (enabledBands.size && !enabledBands.has(s.band)) return false;
  if (searchTerm) {
    const hay = `${s.callsign} ${s.program || ""} ${s.activity_ref || ""} ${s.activity_name || ""} ${s.location_desc || ""} ${s.country || ""} ${s.state || ""} ${s.comment || ""}`.toLowerCase();
    if (!hay.includes(searchTerm)) return false;
  }
  return true;
}

function rowHTML(s, isNew) {
  const activity = (() => {
    if (s.activity_ref) {
      const tag = s.program ? `<span class="prog ${s.program}">${s.program}</span>` : "";
      const ref = `<span class="park">${escapeHtml(s.activity_ref)}</span>`;
      const name = s.activity_name ? " · " + escapeHtml(s.activity_name) : "";
      const extra = s.activity_extra ? `  <span style="color:var(--muted)">${escapeHtml(s.activity_extra)}</span>` : "";
      return `${tag}${ref}${name}${extra}`;
    }
    return escapeHtml(s.location_desc || "");
  })();
  const country = (() => {
    if (s.country && s.state) return `${escapeHtml(s.country)} <span class="state">· ${escapeHtml(s.state)}</span>`;
    if (s.country) return escapeHtml(s.country);
    if (s.state) return escapeHtml(s.state);
    return "";
  })();
  const cls = isNew ? "new-row" : "";
  return `<tr class="${cls}" data-freq="${s.frequency_khz}" data-mode="${escapeHtml(s.mode)}" title="Radio control is in the full Python daemon — see Help → Get the full version">
    <td>${fmtTime(s.spotted_at)}</td>
    <td class="src-${s.source}">${s.source}</td>
    <td class="call">${escapeHtml(s.callsign)}</td>
    <td class="freq">${s.frequency_khz.toFixed(1)}</td>
    <td>${s.band || ""}</td>
    <td>${escapeHtml(s.mode)}</td>
    <td class="country">${country}</td>
    <td>${activity}</td>
    <td>${escapeHtml(s.spotter || "")}</td>
    <td class="comment">${escapeHtml(s.comment || "")}</td>
  </tr>`;
}

// Chronological sort, newest first. Computed once per insertion, not per
// rerender, so filter toggles stay snappy.
function sortSpots() {
  allSpots.sort((a, b) => {
    const ta = a._t ?? (a._t = Date.parse(a.spotted_at) || 0);
    const tb = b._t ?? (b._t = Date.parse(b.spotted_at) || 0);
    return tb - ta;
  });
}

function rerender() {
  const filtered = allSpots.filter(matches);
  const visible = isFinite(maxVisible) ? filtered.slice(0, maxVisible) : filtered;
  rowsEl.innerHTML = visible.map((s) => rowHTML(s, !!s._justArrived)).join("");
  countEl.textContent = `${visible.length} / ${filtered.length}`;
}

function addSpot(s) {
  allSpots.push(s);
  sortSpots();
  if (allSpots.length > MAX_KEEP) allSpots.length = MAX_KEEP;
  s._justArrived = true;
  rerender();
  // The flash animation is CSS-driven; clearing the flag right after the
  // render keeps subsequent rerenders calm.
  delete s._justArrived;
}

// ---------------------------------------------------------------------------
// Row clicks → friendly "go install the full version" toast.
// ---------------------------------------------------------------------------

let toastTimer = null;
function flashToast(msg) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.style.cssText =
      "position:fixed;bottom:46px;left:50%;transform:translateX(-50%);" +
      "background:var(--panel);border:1px solid var(--border);color:var(--fg);" +
      "padding:8px 14px;border-radius:6px;z-index:50;max-width:80vw;" +
      "box-shadow:0 4px 14px rgba(0,0,0,0.4);font-size:12px;transition:opacity .25s;";
    document.body.appendChild(el);
  }
  el.innerHTML = msg;
  el.style.opacity = "1";
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.style.opacity = "0"; }, 4500);
}

rowsEl.addEventListener("click", (e) => {
  const tr = e.target.closest("tr");
  if (!tr || !rowsEl.contains(tr)) return;
  const sel = window.getSelection && window.getSelection();
  if (sel && sel.toString().length > 0) return;
  flashToast(
    'Radio control is in the full Python daemon — ' +
    '<a href="https://github.com/quantnmr/cwsigfind#full-python-daemon" ' +
    'style="color:var(--accent)">install instructions in Help → Get the full version</a>.'
  );
});

// ---------------------------------------------------------------------------
// Beacons widget — ticks every 10s, recomputed locally.
// ---------------------------------------------------------------------------

const beaconsEl = document.getElementById("beacons");
const beaconsBody = document.getElementById("beaconsBody");
const beaconsHead = document.getElementById("beaconsHead");
const beaconsToggle = document.getElementById("beaconsToggle");

if (lsGet(LSK.beaconsCollapsed, "0") === "1") {
  beaconsEl.classList.add("collapsed");
  beaconsToggle.textContent = "[show]";
}
beaconsHead.onclick = () => {
  const collapsed = beaconsEl.classList.toggle("collapsed");
  beaconsToggle.textContent = collapsed ? "[show]" : "[hide]";
  lsSet(LSK.beaconsCollapsed, collapsed ? "1" : "0");
};

function refreshBeacons() {
  const beacons = currentBeacons();
  beaconsBody.innerHTML = beacons.map((b) => {
    const loc = b.location ? escapeHtml(b.location) : "";
    return (
      `<div class="brow" data-freq="${b.frequency_khz}" data-call="${escapeHtml(b.callsign)}" ` +
      `title="${escapeHtml(b.callsign)}${b.location ? ` (${b.location})` : ""} · ${b.frequency_khz.toFixed(0)} kHz CW">` +
      `<span class="b">${escapeHtml(b.band)}</span>` +
      `<span class="c">${escapeHtml(b.callsign)}` +
        (loc ? `<span class="loc">${loc}</span>` : "") +
      `</span>` +
      `<span class="f">${b.frequency_khz.toFixed(0)}</span>` +
      `</div>`
    );
  }).join("");
}
beaconsBody.addEventListener("click", () => {
  flashToast(
    'Radio control is in the full Python daemon — ' +
    '<a href="https://github.com/quantnmr/cwsigfind#full-python-daemon" ' +
    'style="color:var(--accent)">install link in Help</a>.'
  );
});
refreshBeacons();
setInterval(refreshBeacons, 10_000);

// ---------------------------------------------------------------------------
// Propagation indices widget — bottom-right twin of the beacons panel.
// Pulls from NOAA SWPC (browser-callable CORS) every 15 minutes; see
// ./lib/propagation.js for the source-by-source composition.
// ---------------------------------------------------------------------------

const propagationEl = document.getElementById("propagation");
const propagationBody = document.getElementById("propagationBody");
const propagationHead = document.getElementById("propagationHead");
const propagationToggle = document.getElementById("propagationToggle");
const propagationStale = document.getElementById("propagationStale");

if (lsGet(LSK.propagationCollapsed, "0") === "1") {
  propagationEl.classList.add("collapsed");
  propagationToggle.textContent = "[show]";
}
propagationHead.onclick = () => {
  const collapsed = propagationEl.classList.toggle("collapsed");
  propagationToggle.textContent = collapsed ? "[show]" : "[hide]";
  lsSet(LSK.propagationCollapsed, collapsed ? "1" : "0");
};

// Color tiers — kept in sync with the full daemon's tier choices so both
// versions agree on what "good" SFI / "stormy" K means.
function sfiTier(n) {
  if (n == null) return "muted";
  if (n < 70) return "dim";
  if (n < 100) return "ok";
  if (n < 150) return "good";
  return "great";
}
function kTier(n) {
  if (n == null) return "muted";
  if (n <= 2) return "ok";
  if (n <= 4) return "amber";
  return "red";
}
function aTier(n) {
  if (n == null) return "muted";
  if (n <= 7) return "ok";
  if (n <= 15) return "amber";
  return "red";
}
function xrayTier(s) {
  if (!s) return "muted";
  const letter = String(s).trim().charAt(0).toUpperCase();
  if (letter === "A" || letter === "B") return "muted";
  if (letter === "C") return "neutral";
  if (letter === "M") return "amber";
  if (letter === "X") return "red";
  return "muted";
}

function fmtVal(v) { return v == null ? "—" : String(v); }

function fmtUpdated(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm}Z`;
}

function renderPropagation(snap) {
  const hasError = !!(snap && snap.error);
  propagationEl.classList.toggle("stale", hasError);
  propagationStale.hidden = !hasError;
  if (hasError) propagationStale.title = snap.error;
  else propagationStale.removeAttribute("title");

  const sfiClass = sfiTier(snap.sfi);
  const kClass = kTier(snap.k_index);
  const aClass = aTier(snap.a_index);
  const xClass = xrayTier(snap.xray);
  const ssnLabel = snap.ssn_label || "Daily SSN";

  const updatedTxt = fmtUpdated(snap.updated);
  const updatedTitle = snap.updated
    ? `Upstream timestamp (UTC): ${snap.updated}`
    : "No upstream timestamp yet";

  propagationBody.innerHTML = `
    <div class="indices">
      <div class="ix" title="Solar Flux Index at 2800 MHz (10.7 cm), daily — higher is better for HF">
        <span class="lbl">SFI</span>
        <span class="val ${sfiClass}">${fmtVal(snap.sfi)}</span>
      </div>
      <div class="ix" title="${escapeHtml(ssnLabel)} from NOAA SWPC — tracks the solar cycle">
        <span class="lbl">SSN</span>
        <span class="val ${snap.ssn == null ? "muted" : "neutral"}">${fmtVal(snap.ssn)}</span>
      </div>
      <div class="ix" title="Planetary A-index — 24-hour geomagnetic activity; lower is better">
        <span class="lbl">A</span>
        <span class="val ${aClass}">${fmtVal(snap.a_index)}</span>
      </div>
      <div class="ix" title="Planetary K-index — 3-hour geomagnetic activity (0–9); 0–2 quiet, 5+ stormy">
        <span class="lbl">K</span>
        <span class="val ${kClass}">${fmtVal(snap.k_index)}</span>
      </div>
      <div class="ix" title="GOES X-ray class (A/B/C/M/X) — M and X can briefly black out HF">
        <span class="lbl">X-ray</span>
        <span class="val ${xClass}">${escapeHtml(snap.xray || "—")}</span>
      </div>
    </div>
    <div class="lite-note" title="The full Python daemon polls hamqsl.com and adds a Good/Fair/Poor table for the four hamqsl band buckets.">
      Lite build is index-only. The
      <a href="https://github.com/quantnmr/cwsigfind#full-python-daemon" target="_blank" rel="noopener">full Python daemon</a>
      adds per-band HF Good/Fair/Poor.
    </div>
    <div class="meta">
      <span title="${escapeHtml(updatedTitle)}">Updated ${escapeHtml(updatedTxt)}</span>
      <a href="https://www.swpc.noaa.gov/" target="_blank" rel="noopener"
         title="NOAA Space Weather Prediction Center">NOAA SWPC</a>
    </div>
  `;
}

// Boot the panel with an obvious "loading" state so the user sees the panel
// shell immediately, even before the first NOAA fetch returns.
renderPropagation({
  sfi: null, ssn: null, a_index: null, k_index: null, xray: null,
  hf_conditions: {}, updated: null, error: null,
});
startPropagationPoller((snap) => renderPropagation(snap), 15 * 60 * 1000);

// ---------------------------------------------------------------------------
// Source freshness widget (rendered inside Help → Overview).
// ---------------------------------------------------------------------------

function fmtAge(d) {
  if (!d) return "never";
  const ageSec = (Date.now() - d.getTime()) / 1000;
  if (ageSec < 60) return `${ageSec.toFixed(0)}s ago`;
  if (ageSec < 3600) return `${(ageSec / 60).toFixed(0)}m ago`;
  return `${(ageSec / 3600).toFixed(1)}h ago`;
}
function refreshSourceStatus() {
  const last = getLastUpdated();
  for (const span of sourceStatusEl.querySelectorAll("[data-src]")) {
    const src = span.dataset.src;
    const d = last[src];
    span.textContent = fmtAge(d);
    span.className = d ? "ok" : "none";
  }
}
setInterval(refreshSourceStatus, 5_000);

// ---------------------------------------------------------------------------
// Help drawer (same UX as the full app: nav rail + scrollable content).
// ---------------------------------------------------------------------------

const helpDrawer = document.getElementById("helpDrawer");
const backdrop = document.getElementById("drawerBackdrop");
const helpNav = document.getElementById("helpNav");
const helpContent = document.getElementById("helpContent");

function openHelp() {
  helpDrawer.classList.add("open");
  backdrop.classList.add("open");
  helpDrawer.setAttribute("aria-hidden", "false");
  refreshSourceStatus();
}
function closeHelp() {
  helpDrawer.classList.remove("open");
  backdrop.classList.remove("open");
  helpDrawer.setAttribute("aria-hidden", "true");
}
document.getElementById("openHelp").onclick = openHelp;
document.getElementById("closeHelp").onclick = closeHelp;
document.getElementById("openHelpFooter").onclick = (e) => { e.preventDefault(); openHelp(); };
backdrop.onclick = closeHelp;

helpNav.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-sec]");
  if (!btn) return;
  const sec = btn.dataset.sec;
  for (const b of helpNav.querySelectorAll("button")) b.classList.toggle("active", b === btn);
  for (const s of helpContent.querySelectorAll(".help-section")) s.classList.toggle("active", s.dataset.sec === sec);
  helpContent.scrollTop = 0;
});

// "?" opens help, Esc closes — same keyboard guards as the full app so the
// search input stays usable.
document.addEventListener("keydown", (e) => {
  const t = e.target;
  const inField = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
    || t.tagName === "SELECT" || t.isContentEditable);
  if (e.key === "Escape") {
    if (helpDrawer.classList.contains("open")) { closeHelp(); e.preventDefault(); }
    return;
  }
  if (e.key === "?" && !inField && !e.ctrlKey && !e.metaKey && !e.altKey) {
    if (helpDrawer.classList.contains("open")) closeHelp();
    else openHelp();
    e.preventDefault();
  }
});

// ---------------------------------------------------------------------------
// Boot.
// ---------------------------------------------------------------------------

(async () => {
  // Catalog load is best-effort; pollers don't depend on it.
  const n = await loadIotaCatalog();
  console.info(`IOTA catalog: ${n} groups loaded`);
  startAllPollers(addSpot);
})();
