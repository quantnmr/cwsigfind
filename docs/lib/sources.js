// Browser-side spot pollers for the four CORS-friendly feeds.
//
// Each source is its own self-supervising async loop. They all share the same
// shape: fetch JSON → normalize each entry into a common Spot object → push
// non-duplicates to the onSpot() callback. Errors back off exponentially up
// to a 5-minute cap so a flaky upstream doesn't pin the page.
//
// The Spot shape matches what the Python daemon produces (see spot.py), so
// the same rendering logic can be reused 1:1.

import { countryForCallsign, enrich } from "./geo.js";
import { dedupKey, freqToBand } from "./spot.js";

const UA_HINT = "cwsigfind-lite/1.0 (+https://github.com/quantnmr/cwsigfind)";

// Per-source state. The IOTA group catalog is loaded once at boot and used
// to upgrade any IOTA-tagged spot's activity_name (no live IOTA feed exists
// in the lite build; the catalog lookup is a no-op for now but primes the
// code path for a future cluster proxy that emits IOTA spots).
const state = {
  iotaGroups: new Map(),
  lastUpdated: { POTA: null, SOTA: null, WWFF: null, BOTA: null },
};

export function getLastUpdated() {
  return { ...state.lastUpdated };
}

export async function loadIotaCatalog(url = "./iota_groups.json") {
  try {
    const r = await fetch(url, { cache: "no-cache" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const payload = await r.json();
    if (!Array.isArray(payload)) throw new Error("not an array");
    state.iotaGroups.clear();
    for (const row of payload) {
      if (row && row.refno && row.name) {
        state.iotaGroups.set(String(row.refno).toUpperCase(), String(row.name).trim());
      }
    }
    return state.iotaGroups.size;
  } catch (e) {
    // Catalog is a nicety, not a hard requirement.
    console.warn("IOTA catalog load failed:", e);
    return 0;
  }
}

function iotaName(ref) {
  if (!ref) return null;
  return state.iotaGroups.get(String(ref).toUpperCase()) || null;
}

// ---- Normalizers -----------------------------------------------------------

// POTA, SOTA and WWBOTA all emit ISO timestamps that are *actually* UTC but
// often arrive without a trailing "Z" or +HH:MM offset. JS's `new Date(s)`
// silently parses such strings as local time, which would shift all timestamps
// in the UI by the user's TZ. Match the Python daemon's behavior: when no
// timezone marker is present, assume UTC.
function parseIsoMaybe(raw) {
  if (!raw) return new Date();
  let s = String(raw).trim();
  const hasTzMarker =
    s.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(s);
  if (!hasTzMarker) s = s + "Z";
  const d = new Date(s);
  if (isNaN(d.getTime())) return new Date();
  return d;
}

function finishSpot(spot) {
  spot.band = freqToBand(spot.frequency_khz);
  return spot;
}

function normalizePota(raw) {
  const freq = Number(raw.frequency);
  if (!raw || !Number.isFinite(freq)) return null;
  const callsign = String(raw.activator || "").toUpperCase().trim();
  if (!callsign) return null;
  const [country, stateName] = enrich(callsign, raw.locationDesc);
  return finishSpot({
    source: "POTA",
    callsign,
    frequency_khz: freq,
    mode: (raw.mode || "").toUpperCase().trim() || "UNKNOWN",
    spotter: raw.spotter || null,
    comment: raw.comments || null,
    spotted_at: parseIsoMaybe(raw.spotTime).toISOString(),
    program: "POTA",
    activity_ref: raw.reference || null,
    activity_name: raw.parkName || raw.name || null,
    activity_extra: null,
    location_desc: raw.locationDesc || null,
    country,
    state: stateName,
    source_id: raw.spotId != null ? String(raw.spotId) : null,
  });
}

function parseSummitDetails(details) {
  if (!details) return [null, null];
  const parts = String(details).split(",").map((p) => p.trim()).filter(Boolean);
  if (!parts.length) return [null, null];
  const name = parts[0];
  const extras = parts.slice(1).map((p) => p.replace(/\bpoints?\b/gi, "pts"));
  return [name, extras.length ? extras.join(" · ") : null];
}

function normalizeSota(raw) {
  if (!raw) return null;
  const mhz = Number(raw.frequency);
  if (!Number.isFinite(mhz)) return null;
  const callsign = String(raw.activatorCallsign || raw.callsign || "")
    .toUpperCase()
    .trim();
  if (!callsign) return null;
  const association = String(raw.associationCode || "").toUpperCase().trim();
  const region = String(raw.summitCode || "").toUpperCase().trim();
  const summitRef = association && region
    ? `${association}/${region}`
    : region || association || null;
  const [name, extra] = parseSummitDetails(raw.summitDetails);
  // Country from the summit's association code, falling back to activator call.
  const country =
    (association ? countryForCallsign(association) : null) ||
    countryForCallsign(callsign);
  return finishSpot({
    source: "SOTA",
    callsign,
    frequency_khz: mhz * 1000.0,
    mode: (raw.mode || "").toUpperCase().trim() || "UNKNOWN",
    spotter: raw.callsign && raw.callsign !== callsign ? raw.callsign : null,
    comment: (raw.comments || "").trim() || null,
    spotted_at: parseIsoMaybe(raw.timeStamp).toISOString(),
    program: "SOTA",
    activity_ref: summitRef,
    activity_name: name,
    activity_extra: extra,
    country,
    state: null,
    source_id: raw.id != null ? String(raw.id) : null,
  });
}

function normalizeWwff(raw) {
  if (!raw) return null;
  const freq = Number(raw.frequency_khz);
  if (!Number.isFinite(freq)) return null;
  const callsign = String(raw.activator || "").toUpperCase().trim();
  if (!callsign) return null;
  return finishSpot({
    source: "WWFF",
    callsign,
    frequency_khz: freq,
    mode: (raw.mode || "").toUpperCase().trim() || "UNKNOWN",
    spotter: raw.spotter || null,
    comment: (raw.remarks || "").trim() || null,
    spotted_at: raw.spot_time
      ? new Date(Number(raw.spot_time) * 1000).toISOString()
      : new Date().toISOString(),
    program: "WWFF",
    activity_ref: raw.reference || null,
    activity_name: raw.reference_name || null,
    activity_extra: null,
    country: countryForCallsign(callsign),
    state: null,
    source_id: raw.id != null ? String(raw.id) : null,
  });
}

function stableBotaId(raw) {
  const { time, call, freq } = raw || {};
  const type = (raw && raw.type) || "Live";
  if (time && call && freq != null) return `${call}:${type}:${time}:${freq}`;
  return null;
}

function normalizeBota(raw) {
  if (!raw) return null;
  const mhz = Number(raw.freq);
  if (!Number.isFinite(mhz)) return null;
  const callsign = String(raw.call || "").toUpperCase().trim();
  if (!callsign) return null;

  let ref = null;
  let refName = null;
  const refs = Array.isArray(raw.references) ? raw.references : null;
  if (refs && refs.length && refs[0] && typeof refs[0] === "object") {
    ref = refs[0].reference || null;
    refName = refs[0].name || null;
  }
  if (!ref) {
    const m = String(raw.comment || "").match(/\bB\/[A-Z0-9]{1,3}-\d{4}\b/);
    if (m) ref = m[0];
  }

  const spotType = raw.type || "Live";
  const baseComment = (raw.comment || "").trim();
  const comment =
    spotType && spotType !== "Live"
      ? `[${spotType}] ${baseComment}`.trim()
      : baseComment || null;

  return finishSpot({
    source: "BOTA",
    callsign,
    frequency_khz: mhz * 1000.0,
    mode: (raw.mode || "").toUpperCase().trim() || "UNKNOWN",
    spotter: raw.spotter || null,
    comment,
    spotted_at: parseIsoMaybe(raw.time).toISOString(),
    program: "BOTA",
    activity_ref: ref,
    activity_name: refName,
    activity_extra: null,
    country: countryForCallsign(callsign),
    state: null,
    source_id: stableBotaId(raw),
  });
}

// ---- Poll driver -----------------------------------------------------------

// Common loop used by every source. Polls forever; backs off exponentially on
// failures. Each tick fetches, normalizes, and pushes through the dedup gate
// before invoking onSpot.
async function startPoller({ name, url, intervalMs, normalize, onSpot, fetchInit }) {
  // Bounded LRU-ish dedup set so a long-running tab doesn't grow unbounded.
  const seen = new Map();
  const MAX_SEEN = 5000;

  let backoff = intervalMs;
  while (true) {
    try {
      const r = await fetch(url, {
        cache: "no-store",
        // Browsers strip most headers cross-origin anyway, but a UA hint is
        // friendly for log-archeology on the upstream side.
        headers: { Accept: "application/json" },
        ...fetchInit,
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const payload = await r.json();
      if (!Array.isArray(payload)) throw new Error("not an array");

      let newCount = 0;
      for (const raw of payload) {
        let spot;
        try {
          spot = normalize(raw);
        } catch (e) {
          continue;
        }
        if (!spot) continue;
        const key = dedupKey(spot);
        if (seen.has(key)) continue;
        seen.set(key, 1);
        if (seen.size > MAX_SEEN) {
          // Drop the oldest insertion to keep the set bounded.
          const firstKey = seen.keys().next().value;
          if (firstKey !== undefined) seen.delete(firstKey);
        }
        try { onSpot(spot); } catch (e) { console.warn(`${name} onSpot threw`, e); }
        newCount++;
      }
      state.lastUpdated[name] = new Date();
      backoff = intervalMs; // success → reset backoff
      console.debug(`[${name}] +${newCount} new (poll ${payload.length} total)`);
    } catch (e) {
      // 60s base → up to 5 minutes on persistent failures.
      backoff = Math.min(Math.max(backoff * 2, intervalMs), 5 * 60 * 1000);
      console.warn(`[${name}] poll failed: ${e}; retrying in ${(backoff / 1000) | 0}s`);
    }
    await new Promise((resolve) => setTimeout(resolve, backoff));
  }
}

export function startAllPollers(onSpot) {
  // POTA — 30s, sized for ~100-150 active spots.
  startPoller({
    name: "POTA",
    url: "https://api.pota.app/spot/activator",
    intervalMs: 30_000,
    normalize: normalizePota,
    onSpot,
  });

  // SOTA — 30s, sized for ~30-50 active spots.
  startPoller({
    name: "SOTA",
    url: "https://api2.sota.org.uk/api/spots/-1/all",
    intervalMs: 30_000,
    normalize: normalizeSota,
    onSpot,
  });

  // WWFF — minimum 30s by upstream's request; we obey.
  startPoller({
    name: "WWFF",
    url: "https://spots.wwff.co/static/spots.json",
    intervalMs: 30_000,
    normalize: normalizeWwff,
    onSpot,
  });

  // WWBOTA — minimum 60s by upstream's request; age=1h to stay polite.
  startPoller({
    name: "BOTA",
    url: "https://api.wwbota.org/spots/?age=1",
    intervalMs: 60_000,
    normalize: normalizeBota,
    onSpot,
  });
}

// Exposed for unit-testing / console debugging.
export const _internal = { normalizePota, normalizeSota, normalizeWwff, normalizeBota, iotaName };
