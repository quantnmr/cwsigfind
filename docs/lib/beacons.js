// NCDXF / IARU International Beacon schedule — JS port of beacons.py.
// 18 stations × 10s slots × 5 HF bands, repeating every 3 minutes.
// Pure-functional and dependency-free; given UTC time, returns the five
// callsigns currently transmitting (one per band).

export const BEACONS = [
  "4U1UN",   // 0
  "VE8AT",   // 1
  "W6WX",    // 2
  "KH6RS",   // 3
  "ZL6B",    // 4
  "VK6RBP",  // 5
  "JA2IGY",  // 6
  "RR9O",    // 7
  "VR2B",    // 8
  "4S7B",    // 9
  "ZS6DN",   // 10
  "5Z4B",    // 11
  "4X6TU",   // 12
  "OH2B",    // 13
  "CS3B",    // 14
  "LU4AA",   // 15
  "OA4B",    // 16
  "YV5B",    // 17
];

export const BEACON_LOCATIONS = [
  "United Nations",
  "Canada",
  "USA",
  "Hawaii",
  "New Zealand",
  "Australia",
  "Japan",
  "Russia",
  "Hong Kong",
  "Sri Lanka",
  "South Africa",
  "Kenya",
  "Israel",
  "Finland",
  "Madeira",
  "Argentina",
  "Peru",
  "Venezuela",
];

export const BAND_FREQS_KHZ = [14100.0, 18110.0, 21150.0, 24930.0, 28200.0];
export const BAND_NAMES = ["20m", "17m", "15m", "12m", "10m"];

const SLOT_SECONDS = 10;
const CYCLE_SECONDS = SLOT_SECONDS * BEACONS.length; // 180s

// Slot index 0..17 for the given UTC moment.
function slotIndexFor(now) {
  const epochSeconds = Math.floor(now.getTime() / 1000);
  return Math.floor((epochSeconds % CYCLE_SECONDS) / SLOT_SECONDS);
}

export function currentBeacons(now) {
  const t = now instanceof Date ? now : new Date();
  const slot = slotIndexFor(t);
  const out = [];
  for (let b = 0; b < BAND_FREQS_KHZ.length; b++) {
    // station i transmits on band b during slot (i + b) mod 18; inverting:
    // at slot `slot` the station on band `b` is (slot - b) mod 18.
    const stationIdx = ((slot - b) % BEACONS.length + BEACONS.length) % BEACONS.length;
    out.push({
      band: BAND_NAMES[b],
      frequency_khz: BAND_FREQS_KHZ[b],
      callsign: BEACONS[stationIdx],
      location: BEACON_LOCATIONS[stationIdx],
    });
  }
  return out;
}
