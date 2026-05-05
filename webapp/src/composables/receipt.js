// Pure helpers for the Serbian fiscal-receipt QR payload (suf.purs.gov.rs).
// Extracted from the legacy app.js parseReceiptUrl so it can be unit
// tested without DOM or scanner state.

const FISCAL_HOST_FRAGMENT = "suf.purs.gov.rs";

export function isFiscalReceiptUrl(text) {
  return typeof text === "string" && text.includes(FISCAL_HOST_FRAGMENT);
}

export function parseReceiptUrl(url) {
  const vl = new URL(url).searchParams.get("vl");
  if (!vl) throw new Error("No vl parameter");

  const bin = Uint8Array.from(atob(vl), (c) => c.charCodeAt(0));
  const view = new DataView(bin.buffer);

  const amountRaw = view.getBigUint64(25, true);
  const amount = Number(amountRaw) / 10000;

  const msHi = view.getUint32(33, false);
  const msLo = view.getUint32(37, false);
  const ms = msHi * 0x100000000 + msLo;

  const date = new Date(ms).toISOString().slice(0, 10);
  return { amount, date };
}
