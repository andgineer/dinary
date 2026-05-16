// Module-level singleton so the zbar-wasm bundle survives tab switches.
// The component instance's _zbar ref was resetting on every AddView unmount,
// causing a fresh CDN fetch (failing offline) on every return to the Add tab.
let _cache = null;

export async function loadZbar() {
  if (_cache) return _cache;
  try {
    _cache = await import(
      /* @vite-ignore */
      "https://cdn.jsdelivr.net/npm/@undecaf/zbar-wasm@0.11.0/dist/inlined/index.mjs"
    );
    return _cache;
  } catch {
    _cache = null; // don't freeze on transient failure
    throw new Error("QR scanner not available offline");
  }
}
