import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { apiRequest } from "../src/api/_request.js";
import { readServerReach } from "../src/api/serverReach.js";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("HTTP client");
});

let originalFetch;

beforeEach(() => {
  originalFetch = globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
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

describe("apiRequest when the server does not answer", () => {
  function hangingFetch() {
    globalThis.fetch = vi.fn(
      (path, init) =>
        new Promise((resolve, reject) => {
          init.signal.addEventListener("abort", () =>
            reject(new DOMException("The operation was aborted.", "AbortError")),
          );
        }),
    );
  }

  function failingFetch(error) {
    globalThis.fetch = vi.fn(async () => {
      throw error;
    });
  }

  it("attaches no time limit unless asked", async () => {
    mockFetch(200, {});
    await apiRequest("/api/version");
    expect(globalThis.fetch.mock.calls[0][1].signal).toBeUndefined();
  });

  it("calls a request that outlives its time limit a server that does not answer", async () => {
    vi.useFakeTimers();
    hangingFetch();

    const request = apiRequest("/api/expenses", { method: "POST", body: {}, timeoutMs: 30_000 });
    const settled = expect(request).rejects.toMatchObject({
      name: "ServerUnreachable",
      kind: "no-answer",
      message: "The server isn't answering.",
    });
    await vi.advanceTimersByTimeAsync(29_999);
    expect(readServerReach().failure).toBeNull();
    await vi.advanceTimersByTimeAsync(1);

    await settled;
    expect(readServerReach().failure.kind).toBe("no-answer");
  });

  it("tells a device with no network from a server it cannot reach", async () => {
    failingFetch(new TypeError("Load failed"));
    vi.stubGlobal("navigator", { onLine: true });
    await expect(apiRequest("/api/version")).rejects.toMatchObject({ kind: "unreachable" });

    vi.stubGlobal("navigator", { onLine: false });
    await expect(apiRequest("/api/version")).rejects.toMatchObject({ kind: "offline" });
  });

  it("reads a 502 as the VM up and the app down, not as an answer", async () => {
    mockFetch(502, undefined);

    await expect(apiRequest("/api/expenses")).rejects.toMatchObject({
      kind: "app-down",
      message: "The server is on, but dinary isn't running on it.",
    });
    expect(readServerReach()).toMatchObject({ answeredAt: null, failure: { kind: "app-down" } });
  });

  it("counts any reply of the app, an error too, as the server answering", async () => {
    failingFetch(new TypeError("Load failed"));
    await apiRequest("/api/version").catch(() => {});
    mockFetch(404, { detail: "Expense not found" });

    await expect(apiRequest("/api/expenses/1")).rejects.toMatchObject({ status: 404 });

    expect(readServerReach().failure).toBeNull();
    expect(readServerReach().answeredAt).toBeGreaterThan(0);
  });

  it("passes an error that is not the network's through unchanged", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
    }));

    await expect(apiRequest("/api/version")).rejects.toBeInstanceOf(SyntaxError);
    expect(readServerReach().failure).toBeNull();
  });
});
