// Browser-side propagation indices for the lite demo.
//
// hamqsl.com (the source the full Python daemon uses) does not advertise CORS
// headers, so the lite build can't hit it from a browser. Instead we compose
// the same `PropagationSnapshot` shape from a handful of NOAA SWPC endpoints
// that all advertise `Access-Control-Allow-Origin: *`. The lite snapshot is
// index-only: it does NOT include the band-by-band Good/Fair/Poor table that
// hamqsl provides (that's a value-add of the full Python daemon).
//
// Each endpoint is fetched independently; if any one fails the others still
// populate (graceful degradation). The composed snapshot keeps the last-known
// value for any field whose endpoint failed this tick and surfaces an
// aggregate `error` string for the UI to flag as stale.
//
// Source shapes (verified live, May 2026):
//   - /products/summary/10cm-flux.json        → [{flux, time_tag}]
//   - /products/noaa-planetary-k-index.json   → [{time_tag, Kp, a_running, station_count}, ...]
//   - /json/goes/primary/xrays-7-day.json     → [{time_tag, satellite, flux, energy, ...}, ...]
//   - /json/solar-cycle/swpc_observed_ssn.json → [{Obsdate, swpc_ssn}, ...] (daily!)

const NOAA = {
  flux10cm: "https://services.swpc.noaa.gov/products/summary/10cm-flux.json",
  kIndex: "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
  xray: "https://services.swpc.noaa.gov/json/goes/primary/xrays-7-day.json",
  ssn: "https://services.swpc.noaa.gov/json/solar-cycle/swpc_observed_ssn.json",
};

export const SOURCE_LABEL = "NOAA SWPC";
// SSN endpoint above is the SWPC daily observed sunspot number — not a
// monthly file. We label it accordingly in the UI tooltip.
export const SSN_LABEL = "Daily SSN";

// GOES X-ray flux thresholds (W/m²) → A/B/C/M/X letter class + sub-digit.
// Standard NOAA classification.
function classifyXray(flux) {
  if (!Number.isFinite(flux) || flux <= 0) return null;
  // letter ranges in W/m²:
  //   A < 1e-7, B 1e-7..1e-6, C 1e-6..1e-5, M 1e-5..1e-4, X >= 1e-4
  let letter, base;
  if (flux < 1e-7) { letter = "A"; base = 1e-8; }
  else if (flux < 1e-6) { letter = "B"; base = 1e-7; }
  else if (flux < 1e-5) { letter = "C"; base = 1e-6; }
  else if (flux < 1e-4) { letter = "M"; base = 1e-5; }
  else { letter = "X"; base = 1e-4; }
  // Sub-digit is the mantissa relative to the band base, rounded to 1 decimal.
  // e.g. flux 2.23e-6 → C2.2; flux 1.0e-4 → X1.0.
  const sub = flux / base;
  return `${letter}${sub.toFixed(1)}`;
}

async function fetchJson(url, timeoutMs = 15000) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const r = await fetch(url, {
      cache: "no-store",
      signal: ac.signal,
      headers: { Accept: "application/json" },
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}

// ---- Per-endpoint parsers -------------------------------------------------

function parseSfi(payload) {
  if (!Array.isArray(payload) || payload.length === 0) return [null, null];
  const last = payload[payload.length - 1];
  const flux = Number(last && last.flux);
  if (!Number.isFinite(flux)) return [null, null];
  return [Math.round(flux), last.time_tag || null];
}

function parseKIndex(payload) {
  // Endpoint emits a header row + data rows OR a list of dicts depending on
  // the day. Both shapes converge cleanly: pick the last row that has Kp.
  if (!Array.isArray(payload) || payload.length === 0) return [null, null, null];
  for (let i = payload.length - 1; i >= 0; i--) {
    const row = payload[i];
    if (!row || typeof row !== "object") continue;
    // Header rows have string Kp ("Kp") — filter to numeric.
    const kp = Number(row.Kp);
    if (!Number.isFinite(kp)) continue;
    const a = Number(row.a_running);
    return [
      Math.round(kp),
      Number.isFinite(a) ? Math.round(a) : null,
      row.time_tag || null,
    ];
  }
  return [null, null, null];
}

function parseXray(payload) {
  if (!Array.isArray(payload) || payload.length === 0) return [null, null];
  // 7-day stream is 1-min cadence with both energy bands interleaved. The
  // class designation is defined on the 0.1-0.8 nm channel; pick the most
  // recent sample for that energy.
  for (let i = payload.length - 1; i >= 0; i--) {
    const row = payload[i];
    if (!row || row.energy !== "0.1-0.8nm") continue;
    const cls = classifyXray(Number(row.flux));
    if (!cls) continue;
    return [cls, row.time_tag || null];
  }
  return [null, null];
}

function parseSsn(payload) {
  if (!Array.isArray(payload) || payload.length === 0) return [null, null];
  const last = payload[payload.length - 1];
  const ssn = Number(last && last.swpc_ssn);
  if (!Number.isFinite(ssn)) return [null, null];
  return [Math.round(ssn), last.Obsdate || last.time_tag || null];
}

// ---- Snapshot composition -------------------------------------------------

// Single empty snapshot mirroring the server-side dataclass shape (minus
// hamqsl-only fields: hf_conditions / vhf_conditions / signal_noise / etc.).
function emptySnapshot() {
  return {
    updated: null,
    sfi: null,
    ssn: null,
    a_index: null,
    k_index: null,
    xray: null,
    // Fields the lite build doesn't compute — kept on the object for shape
    // parity with the server snapshot, but always null.
    helium_line: null,
    proton_flux: null,
    electron_flux: null,
    aurora: null,
    solar_wind: null,
    magnetic_field: null,
    geomag_field: null,
    signal_noise: null,
    muf: null,
    hf_conditions: {},
    vhf_conditions: [],
    source: SOURCE_LABEL,
    ssn_label: SSN_LABEL,
    error: null,
    fetched_at: null,
  };
}

// Module-level singleton so a fetch failure on one endpoint doesn't wipe out
// the last successful value for that field. (Mirrors the server's behavior.)
const snapshot = emptySnapshot();

export function getSnapshot() {
  return { ...snapshot };
}

export async function refreshSnapshot() {
  // Each endpoint independently; collect partial errors so we can surface
  // them on the snapshot.error string without losing successful fields.
  const tasks = [
    fetchJson(NOAA.flux10cm).then((p) => ({ kind: "sfi", value: parseSfi(p) })),
    fetchJson(NOAA.kIndex).then((p) => ({ kind: "k", value: parseKIndex(p) })),
    fetchJson(NOAA.xray).then((p) => ({ kind: "xray", value: parseXray(p) })),
    fetchJson(NOAA.ssn).then((p) => ({ kind: "ssn", value: parseSsn(p) })),
  ];
  const results = await Promise.allSettled(tasks);

  const errors = [];
  // The most recent timestamp across all fetches becomes `updated`.
  let latestTimestamp = null;
  for (const r of results) {
    if (r.status === "rejected") {
      errors.push(r.reason && r.reason.message ? r.reason.message : String(r.reason));
      continue;
    }
    const { kind, value } = r.value;
    if (kind === "sfi") {
      const [sfi, t] = value;
      if (sfi != null) snapshot.sfi = sfi;
      if (t) latestTimestamp = latestTimestamp || t;
    } else if (kind === "k") {
      const [k, a, t] = value;
      if (k != null) snapshot.k_index = k;
      if (a != null) snapshot.a_index = a;
      if (t && (!latestTimestamp || t > latestTimestamp)) latestTimestamp = t;
    } else if (kind === "xray") {
      const [cls, t] = value;
      if (cls) snapshot.xray = cls;
      if (t && (!latestTimestamp || t > latestTimestamp)) latestTimestamp = t;
    } else if (kind === "ssn") {
      const [ssn, _t] = value;
      if (ssn != null) snapshot.ssn = ssn;
    }
  }

  snapshot.updated = latestTimestamp;
  snapshot.fetched_at = new Date().toISOString();
  snapshot.error = errors.length
    ? `${errors.length} of ${results.length} endpoints failed: ${errors.join("; ")}`
    : null;
  return getSnapshot();
}

export function startPropagationPoller(onUpdate, intervalMs = 15 * 60 * 1000) {
  // Run immediately, then on the steady cadence. The interval mirrors the
  // server-side daemon's 15-minute floor; these endpoints aren't high-rate
  // and we want to be a polite client.
  let cancelled = false;
  let backoffMs = intervalMs;
  const tick = async () => {
    if (cancelled) return;
    try {
      const snap = await refreshSnapshot();
      if (snap.error) {
        // Exponential backoff on persistent failures, capped at the steady
        // cadence so we don't slow to a crawl after one transient blip.
        backoffMs = Math.min(intervalMs, Math.max(intervalMs, backoffMs * 1.5));
      } else {
        backoffMs = intervalMs;
      }
      try { onUpdate(snap); } catch (e) { console.warn("propagation onUpdate threw", e); }
    } catch (e) {
      console.warn("propagation refresh crashed", e);
    } finally {
      if (!cancelled) setTimeout(tick, backoffMs);
    }
  };
  tick();
  return () => { cancelled = true; };
}

// Exposed for unit-testing / console debugging.
export const _internal = {
  classifyXray,
  parseSfi,
  parseKIndex,
  parseXray,
  parseSsn,
};
