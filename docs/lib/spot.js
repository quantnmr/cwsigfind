// Band mapping + dedup key for normalized spots — pure data, no DOM.
// Mirrors src/cwsigfind/spot.py one-for-one (same IARU edges, same dedup
// shape) so the lite demo and the Python daemon agree on what counts as
// "the same spot".

// (lowKhz, highKhz, name). Edges are intentionally broad so a slightly
// out-of-band spotter call still maps to the right band chip.
const BANDS = [
  [1800.0, 2000.0, "160m"],
  [3500.0, 4000.0, "80m"],
  [5250.0, 5450.0, "60m"],
  [7000.0, 7300.0, "40m"],
  [10100.0, 10150.0, "30m"],
  [14000.0, 14350.0, "20m"],
  [18068.0, 18168.0, "17m"],
  [21000.0, 21450.0, "15m"],
  [24890.0, 24990.0, "12m"],
  [28000.0, 29700.0, "10m"],
  [50000.0, 54000.0, "6m"],
  [144000.0, 148000.0, "2m"],
  [222000.0, 225000.0, "1.25m"],
  [420000.0, 450000.0, "70cm"],
];

export const BAND_NAMES = [
  "160m", "80m", "60m", "40m", "30m", "20m",
  "17m", "15m", "12m", "10m", "6m", "2m",
];

export function freqToBand(khz) {
  if (typeof khz !== "number" || !Number.isFinite(khz)) return null;
  for (const [lo, hi, name] of BANDS) {
    if (khz >= lo && khz <= hi) return name;
  }
  return null;
}

// Same shape as Spot.dedup_key() in spot.py. POTA / SOTA / WWFF / WWBOTA
// all provide a stable source_id, but we keep the freq+minute fallback for
// safety so the function can be reused for any future telnet-style sources.
export function dedupKey(spot) {
  if (spot.source_id) return `${spot.source}:${spot.source_id}`;
  const t = new Date(spot.spotted_at);
  const minute = new Date(Date.UTC(
    t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate(),
    t.getUTCHours(), t.getUTCMinutes(), 0
  )).toISOString();
  return `${spot.source}:${spot.callsign}:${Math.round(spot.frequency_khz)}:${minute}`;
}
