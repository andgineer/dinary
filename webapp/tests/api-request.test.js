import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { apiRequest } from "../src/api/_request.js";

beforeEach(async () => {
  await allure.epic("API");
  await allure.feature("HTTP request");
});

let originalFetch;

beforeEach(() => {
  originalFetch = globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function mockFetch(status, body) {
  globalThis.fetch = vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: async () => body,
  }));
}

describe("apiRequest error handling", () => {
  it("throws with string detail from server", async () => {
    mockFetch(404, { detail: "Expense not found" });
    await expect(apiRequest("/api/expenses/999")).rejects.toMatchObject({
      message: "Expense not found",
      status: 404,
    });
  });

  it("extracts msg from first pydantic validation error (array detail)", async () => {
    mockFetch(422, {
      detail: [
        {
          type: "int_parsing",
          loc: ["path", "expense_id"],
          msg: "Input should be a valid integer",
          input: "undefined",
        },
      ],
    });
    await expect(apiRequest("/api/expenses/undefined/category", { method: "PATCH", body: {} }))
      .rejects.toMatchObject({
        message: "Input should be a valid integer",
        status: 422,
      });
  });

  it("falls back to HTTP status when detail array has no msg", async () => {
    mockFetch(422, { detail: [{}] });
    await expect(apiRequest("/api/foo")).rejects.toMatchObject({
      message: "HTTP 422",
      status: 422,
    });
  });

  it("falls back to HTTP status when body has no detail", async () => {
    mockFetch(500, {});
    await expect(apiRequest("/api/foo")).rejects.toMatchObject({
      message: "HTTP 500",
      status: 500,
    });
  });

  it("returns parsed JSON on success", async () => {
    mockFetch(200, { id: 1 });
    const result = await apiRequest("/api/foo");
    expect(result).toEqual({ id: 1 });
  });
});
