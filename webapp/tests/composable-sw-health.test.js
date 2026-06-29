import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  reportNetworkFailure,
  reportNetworkSuccess,
  _resetForTest,
} from "../src/composables/swHealth.js";

beforeEach(async () => {
  await allure.epic("PWA");
  await allure.feature("Frontend");
  await allure.story("swHealth");
});

function makeSw(controller = {}) {
  const unregister = vi.fn().mockResolvedValue(true);
  const getRegistrations = vi.fn().mockResolvedValue([{ unregister }]);
  return { sw: { controller, getRegistrations }, unregister };
}

function setOnline(value) {
  Object.defineProperty(navigator, "onLine", { value, configurable: true });
}

function setServiceWorker(value) {
  Object.defineProperty(navigator, "serviceWorker", { value, configurable: true });
}

let reloadMock;

beforeEach(() => {
  _resetForTest();
  sessionStorage.clear();
  setOnline(true);
  const { sw } = makeSw();
  setServiceWorker(sw);
  reloadMock = vi.fn();
  vi.stubGlobal("location", { reload: reloadMock });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("reportNetworkFailure", () => {
  it("does nothing below threshold", () => {
    reportNetworkFailure();
    reportNetworkFailure();
    expect(reloadMock).not.toHaveBeenCalled();
  });

  it("triggers SW reset and reload after 3 consecutive failures", async () => {
    reportNetworkFailure();
    reportNetworkFailure();
    reportNetworkFailure();
    await new Promise((r) => setTimeout(r, 0));
    expect(reloadMock).toHaveBeenCalled();
  });

  it("unregisters all SW registrations before reload", async () => {
    const unregister = vi.fn().mockResolvedValue(true);
    setServiceWorker({
      controller: {},
      getRegistrations: vi.fn().mockResolvedValue([{ unregister }, { unregister }]),
    });
    reportNetworkFailure();
    reportNetworkFailure();
    reportNetworkFailure();
    await new Promise((r) => setTimeout(r, 0));
    expect(unregister).toHaveBeenCalledTimes(2);
  });

  it("does nothing when offline", () => {
    setOnline(false);
    reportNetworkFailure();
    reportNetworkFailure();
    reportNetworkFailure();
    expect(reloadMock).not.toHaveBeenCalled();
  });

  it("does nothing when no SW controller", () => {
    setServiceWorker({ controller: null, getRegistrations: vi.fn() });
    reportNetworkFailure();
    reportNetworkFailure();
    reportNetworkFailure();
    expect(reloadMock).not.toHaveBeenCalled();
  });

  it("does not reload a second time if sw_reset_attempted is already set", async () => {
    sessionStorage.setItem("sw_reset_attempted", "1");
    reportNetworkFailure();
    reportNetworkFailure();
    reportNetworkFailure();
    await new Promise((r) => setTimeout(r, 0));
    expect(reloadMock).not.toHaveBeenCalled();
  });

  it("sets sw_reset_attempted before reloading to prevent loops", async () => {
    reportNetworkFailure();
    reportNetworkFailure();
    reportNetworkFailure();
    await new Promise((r) => setTimeout(r, 0));
    expect(sessionStorage.getItem("sw_reset_attempted")).toBe("1");
  });
});

describe("reportNetworkSuccess", () => {
  it("resets the failure counter so 3 more failures are needed to trigger reset", async () => {
    reportNetworkFailure();
    reportNetworkFailure();
    reportNetworkSuccess();
    reportNetworkFailure();
    reportNetworkFailure();
    await new Promise((r) => setTimeout(r, 0));
    expect(reloadMock).not.toHaveBeenCalled();
  });
});
