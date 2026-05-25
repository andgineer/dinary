import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  etagFor,
  fetchCatalog,
  NotModified,
  adminAddGroup,
  adminAddCategory,
  adminAddEvent,
  adminAddTag,
  adminPatchGroup,
  adminPatchCategory,
  adminPatchEvent,
  adminPatchTag,
  adminReactivateGroup,
  adminDeactivateGroup,
  adminDeleteGroup,
} from "../src/api/catalog.js";

beforeEach(async () => {
  await allure.epic("API");
  await allure.feature("Catalog");
});

let originalFetch;

function okResponse(body = {}) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    json: async () => body,
  };
}

beforeEach(() => {
  originalFetch = globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("etagFor", () => {
  it("formats catalog_version into a weak ETag", () => {
    expect(etagFor(0)).toBe('W/"catalog-v0"');
    expect(etagFor(42)).toBe('W/"catalog-v42"');
  });
});

describe("fetchCatalog", () => {
  it("returns the snapshot when server responds 200", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ catalog_version: 1, category_groups: [] }),
    }));

    const snap = await fetchCatalog();

    expect(snap).toMatchObject({ catalog_version: 1 });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/catalog",
      expect.objectContaining({ headers: {} }),
    );
  });

  it("sends If-None-Match when ifVersion is provided", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ catalog_version: 2 }),
    }));

    await fetchCatalog({ ifVersion: 5 });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/catalog",
      expect.objectContaining({
        headers: { "If-None-Match": 'W/"catalog-v5"' },
      }),
    );
  });

  it("returns NotModified on 304", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 304,
      json: async () => ({}),
    }));

    const out = await fetchCatalog({ ifVersion: 5 });
    expect(out).toBeInstanceOf(NotModified);
    expect(out.notModified).toBe(true);
  });

  it("throws on other non-2xx status", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 500,
      json: async () => ({ detail: "boom" }),
    }));

    await expect(fetchCatalog()).rejects.toThrow("boom");
  });
});

describe("admin POST helpers", () => {
  it("adminAddGroup posts name and sort_order with null defaults", async () => {
    globalThis.fetch = vi.fn(async () =>
      okResponse({ new_id: 1, status: "added", catalog_version: 2 }),
    );

    await adminAddGroup({ name: "food" });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/catalog/groups",
      expect.objectContaining({ method: "POST" }),
    );
    expect(JSON.parse(globalThis.fetch.mock.calls[0][1].body)).toEqual({
      name: "food",
      sort_order: null,
    });
  });

  it("adminAddCategory normalises optional fields", async () => {
    globalThis.fetch = vi.fn(async () => okResponse());
    await adminAddCategory({ name: "cafe", group_id: 3 });
    expect(JSON.parse(globalThis.fetch.mock.calls[0][1].body)).toEqual({
      name: "cafe",
      group_id: 3,
      sheet_name: null,
      sheet_group: null,
    });
  });

  it("adminAddEvent defaults auto_attach_enabled to false and auto_tags to null", async () => {
    globalThis.fetch = vi.fn(async () => okResponse());
    await adminAddEvent({
      name: "trip",
      date_from: "2026-05-01",
      date_to: "2026-05-10",
    });
    expect(JSON.parse(globalThis.fetch.mock.calls[0][1].body)).toEqual({
      name: "trip",
      date_from: "2026-05-01",
      date_to: "2026-05-10",
      auto_attach_enabled: false,
      auto_tags: null,
    });
  });

  it("adminAddTag posts only the name", async () => {
    globalThis.fetch = vi.fn(async () => okResponse());
    await adminAddTag({ name: "vacation" });
    expect(JSON.parse(globalThis.fetch.mock.calls[0][1].body)).toEqual({
      name: "vacation",
    });
  });
});

describe("admin PATCH / DELETE helpers", () => {
  it("adminPatchGroup PATCHes the right path with the body", async () => {
    globalThis.fetch = vi.fn(async () => okResponse());
    await adminPatchGroup(7, { name: "renamed" });
    const [url, init] = globalThis.fetch.mock.calls[0];
    expect(url).toBe("/api/catalog/groups/7");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ name: "renamed" });
  });

  it("reactivate / deactivate helpers flip is_active flag", async () => {
    globalThis.fetch = vi.fn(async () => okResponse());
    await adminReactivateGroup(1);
    expect(JSON.parse(globalThis.fetch.mock.calls[0][1].body)).toEqual({
      is_active: true,
    });
    globalThis.fetch.mockClear();
    await adminDeactivateGroup(1);
    expect(JSON.parse(globalThis.fetch.mock.calls[0][1].body)).toEqual({
      is_active: false,
    });
  });

  it("adminDeleteGroup uses DELETE method", async () => {
    globalThis.fetch = vi.fn(async () => okResponse());
    await adminDeleteGroup(9);
    expect(globalThis.fetch.mock.calls[0][1].method).toBe("DELETE");
  });

  it("propagates server errors with status code", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 409,
      json: async () => ({ detail: "in use" }),
    }));
    await expect(adminPatchCategory(1, {})).rejects.toMatchObject({
      message: "in use",
      status: 409,
    });
  });

  it("category/event/tag PATCH+DELETE share the same shape", async () => {
    globalThis.fetch = vi.fn(async () => okResponse());
    await adminPatchCategory(11, { name: "x" });
    await adminPatchEvent(22, { date_to: "2026-05-12" });
    await adminPatchTag(33, { name: "y" });
    const urls = globalThis.fetch.mock.calls.map((c) => c[0]);
    expect(urls).toEqual([
      "/api/catalog/categories/11",
      "/api/catalog/events/22",
      "/api/catalog/tags/33",
    ]);
  });
});
