// Fixed fill colors for currency pills and picker chips. Every color
// carries white text, so each one keeps at least ~3:1 contrast with #fff.

const KNOWN_COLORS = {
  RSD: "#ea580c",
  EUR: "#2563eb",
  USD: "#16a34a",
  RUB: "#9333ea",
  GBP: "#db2777",
  CHF: "#dc2626",
};

// Picked to stay distinguishable from KNOWN_COLORS, so a less common
// currency never looks like a common one.
const RESERVE_COLORS = ["#0d9488", "#a16207", "#4d7c0f", "#a21caf", "#475569"];

export function currencyColor(code) {
  const upper = typeof code === "string" ? code.trim().toUpperCase() : "";
  if (KNOWN_COLORS[upper]) return KNOWN_COLORS[upper];
  let hash = 0;
  for (const ch of upper) {
    hash = (hash * 31 + ch.charCodeAt(0)) % 1_000_003;
  }
  return RESERVE_COLORS[hash % RESERVE_COLORS.length];
}
