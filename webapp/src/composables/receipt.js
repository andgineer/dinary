// Pure helpers for fiscal-receipt QR payloads.
//
// Serbia (suf.purs.gov.rs): a binary `vl=` query parameter encodes amount and
// timestamp. Amounts are RSD.
// Montenegro (mapr.tax.gov.me / efitest.tax.gov.me): the verify URL carries the
// total (`prc`) and purchase time (`crtd`) as plain parameters that sit after
// the `#` fragment. Amounts are EUR. The `+` timezone-offset sign in `crtd` is
// decoded to a space by URLSearchParams and is restored here.
//
// The country is detected from the URL itself — the user never picks one.

const SERBIAN_HOST_FRAGMENT = "suf.purs.gov.rs";
const MONTENEGRIN_HOST_FRAGMENTS = ["mapr.tax.gov.me", "efitest.tax.gov.me"];

function isSerbianReceiptUrl(text) {
  return text.includes(SERBIAN_HOST_FRAGMENT);
}

function isMontenegrinReceiptUrl(text) {
  return MONTENEGRIN_HOST_FRAGMENTS.some((host) => text.includes(host));
}

export function isFiscalReceiptUrl(text) {
  return (
    typeof text === "string" &&
    (isSerbianReceiptUrl(text) || isMontenegrinReceiptUrl(text))
  );
}

function parseSerbianReceiptUrl(url) {
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
  return { amount, date, currency: "RSD" };
}

function montenegrinParams(url) {
  // The verify URL keeps its parameters after the `#/verify` fragment.
  const parsed = new URL(url);
  const fragmentQuery = parsed.hash.includes("?")
    ? parsed.hash.slice(parsed.hash.indexOf("?") + 1)
    : "";
  const params = new URLSearchParams(
    [parsed.search.replace(/^\?/, ""), fragmentQuery].filter(Boolean).join("&"),
  );
  const crtd = params.get("crtd");
  return {
    prc: params.get("prc"),
    // URLSearchParams decodes the offset `+` to a space; restore it.
    crtd: crtd ? crtd.replace(/ /g, "+") : null,
  };
}

function parseMontenegrinReceiptUrl(url) {
  const { prc, crtd } = montenegrinParams(url);
  if (!prc || !crtd) throw new Error("No prc/crtd parameters");
  const amount = Number.parseFloat(prc);
  if (Number.isNaN(amount)) throw new Error("Invalid prc parameter");
  const date = crtd.slice(0, 10);
  return { amount, date, currency: "EUR" };
}

export function parseReceiptUrl(url) {
  if (isMontenegrinReceiptUrl(url)) return parseMontenegrinReceiptUrl(url);
  return parseSerbianReceiptUrl(url);
}
