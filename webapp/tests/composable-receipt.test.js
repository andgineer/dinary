import { beforeEach, describe, it, expect } from "vitest";
import { isFiscalReceiptUrl, parseReceiptUrl } from "../src/composables/receipt.js";

beforeEach(async () => {
  await allure.epic("Receipts");
  await allure.feature("Frontend");
  await allure.story("Receipt URL parsing");
});

describe("isFiscalReceiptUrl", () => {
  it("recognizes the suf.purs.gov.rs host", () => {
    expect(
      isFiscalReceiptUrl("https://suf.purs.gov.rs/v/?vl=AAAA"),
    ).toBe(true);
    expect(
      isFiscalReceiptUrl("https://example.com/?vl=AAAA"),
    ).toBe(false);
  });

  it("returns false for non-strings", () => {
    expect(isFiscalReceiptUrl(null)).toBe(false);
    expect(isFiscalReceiptUrl(undefined)).toBe(false);
    expect(isFiscalReceiptUrl(123)).toBe(false);
  });
});

describe("parseReceiptUrl", () => {
  function buildVlPayload(amountUnits, ms) {
    // The fiscal receipt v= payload has:
    //   bytes 25..32 : amount (uint64 little-endian, in 1/10000 units)
    //   bytes 33..40 : milliseconds since epoch (big-endian uint64)
    const buf = new Uint8Array(64);
    const view = new DataView(buf.buffer);
    view.setBigUint64(25, BigInt(amountUnits), true);
    const msHi = Math.floor(ms / 0x100000000);
    const msLo = ms % 0x100000000;
    view.setUint32(33, msHi, false);
    view.setUint32(37, msLo, false);
    let bin = "";
    for (const b of buf) bin += String.fromCharCode(b);
    return btoa(bin);
  }

  it("extracts amount (in 1/10000 of a unit) and date from a known payload", () => {
    const ms = Date.UTC(2026, 4, 4, 12, 30, 0);
    const vl = buildVlPayload(1234500, ms);
    const out = parseReceiptUrl(`https://suf.purs.gov.rs/v/?vl=${vl}`);
    expect(out.amount).toBeCloseTo(123.45, 5);
    expect(out.date).toBe("2026-05-04");
  });

  it("throws when the vl parameter is missing", () => {
    expect(() => parseReceiptUrl("https://suf.purs.gov.rs/v/")).toThrow(
      /No vl parameter/,
    );
  });
});
