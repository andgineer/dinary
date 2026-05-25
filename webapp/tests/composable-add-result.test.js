import { beforeEach, describe, it, expect } from "vitest";
import {
  addResultMessage,
  TAG_NAME_DISALLOWED_RE,
  validateTagName,
} from "../src/composables/addResult.js";

beforeEach(async () => {
  await allure.epic("Expenses");
  await allure.feature("Frontend");
  await allure.story("addResult");
});

describe("addResultMessage", () => {
  it("returns the right wording for each kind/status", () => {
    expect(addResultMessage("group", "reactivated")).toContain("group restored");
    expect(addResultMessage("category", "reactivated")).toContain("category restored");
    expect(addResultMessage("event", "noop")).toContain("event already exists");
    expect(addResultMessage("tag", "noop")).toContain("tag already exists");
  });

  it("returns null for created (the unsurprising happy path)", () => {
    expect(addResultMessage("group", "created")).toBeNull();
  });

  it("returns null for unknown kinds or statuses", () => {
    expect(addResultMessage("unknown", "reactivated")).toBeNull();
    expect(addResultMessage("group", "weird")).toBeNull();
  });
});

describe("validateTagName", () => {
  it("rejects empty input", () => {
    expect(validateTagName("")).toBe("Enter a name");
  });

  it("rejects names containing spaces or commas", () => {
    expect(validateTagName("hello world")).toMatch(/spaces or commas/);
    expect(validateTagName("a,b")).toMatch(/spaces or commas/);
  });

  it("accepts valid names", () => {
    expect(validateTagName("vacation")).toBeNull();
    expect(validateTagName("trip-2026")).toBeNull();
  });

  it("TAG_NAME_DISALLOWED_RE matches whitespace and commas", () => {
    expect(TAG_NAME_DISALLOWED_RE.test("a b")).toBe(true);
    expect(TAG_NAME_DISALLOWED_RE.test("a\tb")).toBe(true);
    expect(TAG_NAME_DISALLOWED_RE.test("a,b")).toBe(true);
    expect(TAG_NAME_DISALLOWED_RE.test("clean")).toBe(false);
  });
});
