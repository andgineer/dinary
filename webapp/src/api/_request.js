import { noteAnswer, noteFailure } from "./serverReach.js";

// Tailscale serve (and a Cloudflare tunnel) answers 502 when nothing listens behind
// it, and the app never sends one itself: a 502 means the VM is up and dinary is not.
const APP_DOWN = 502;

function unreached(error) {
  if (error?.name === "AbortError") return noteFailure("no-answer");
  if (error instanceof TypeError) {
    return noteFailure(globalThis.navigator?.onLine === false ? "offline" : "unreachable");
  }
  return error;
}

export async function apiRequest(path, { method = "GET", body, timeoutMs } = {}) {
  const init = { method };
  let timer;
  if (timeoutMs) {
    const controller = new AbortController();
    timer = setTimeout(() => controller.abort(), timeoutMs);
    init.signal = controller.signal;
  }
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  try {
    const resp = await fetch(path, init).catch((error) => {
      throw unreached(error);
    });
    if (resp.status === APP_DOWN) throw noteFailure("app-down");
    noteAnswer();
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      const detail = err.detail;
      const message = Array.isArray(detail)
        ? (detail[0]?.msg || `HTTP ${resp.status}`)
        : (detail || `HTTP ${resp.status}`);
      const e = new Error(message);
      e.status = resp.status;
      throw e;
    }
    if (resp.status === 204 || resp.headers.get("content-length") === "0") return null;
    return await resp.json().catch((error) => {
      throw unreached(error);
    });
  } finally {
    clearTimeout(timer);
  }
}
