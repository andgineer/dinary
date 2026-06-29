// Module-level singleton so the wasm module survives tab switches.
// The component instance ref resets on every AddView unmount,
// causing re-initialization on every return to the Add tab.
let _cache = null;

export async function loadZbar() {
  if (_cache) return _cache;
  try {
    _cache = await import("@undecaf/zbar-wasm");
    return _cache;
  } catch (err) {
    _cache = null;
    throw new Error(`QR scanner failed to initialize: ${err.message}`);
  }
}
