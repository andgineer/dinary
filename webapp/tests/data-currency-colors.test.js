import { beforeEach, describe, it, expect } from "vitest";
import { currencyColor } from "../src/data/currency-colors.js";
import { WORLD_CURRENCIES } from "../src/data/world-currencies.js";

beforeEach(async () => {
  await allure.epic("Currencies");
  await allure.feature("Frontend");
  await allure.story("Currency colors");
});

const COMMON = ["RSD", "EUR", "USD", "RUB", "GBP", "CHF"];

function contrastWithWhite(hex) {
  const channels = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  const luminance = 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  return 1.05 / (luminance + 0.05);
}

describe("currencyColor", () => {
  it("gives every common currency its own color", () => {
    const colors = COMMON.map(currencyColor);
    expect(new Set(colors).size).toBe(COMMON.length);
  });

  it("never gives a less common currency the color of a common one", () => {
    const common = new Set(COMMON.map(currencyColor));
    const others = WORLD_CURRENCIES.map((c) => c.code).filter((c) => !COMMON.includes(c));
    for (const code of others) {
      expect(common.has(currencyColor(code)), code).toBe(false);
    }
  });

  it("is stable and ignores case and whitespace", () => {
    expect(currencyColor("eur")).toBe(currencyColor("EUR"));
    expect(currencyColor(" huf ")).toBe(currencyColor("HUF"));
    expect(currencyColor("HUF")).toBe(currencyColor("HUF"));
  });

  it("returns a hex color for empty or missing codes", () => {
    expect(currencyColor("")).toMatch(/^#[0-9a-f]{6}$/);
    expect(currencyColor(undefined)).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("keeps white pill text readable on every color", () => {
    const codes = [...COMMON, ...WORLD_CURRENCIES.map((c) => c.code)];
    for (const code of codes) {
      expect(contrastWithWhite(currencyColor(code)), code).toBeGreaterThanOrEqual(3);
    }
  });
});
