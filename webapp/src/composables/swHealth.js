let _consecutiveFailures = 0;

export function reportNetworkFailure() {
  if (!navigator.onLine) return;
  if (!navigator.serviceWorker?.controller) return;
  _consecutiveFailures++;
  if (_consecutiveFailures >= 3) {
    void _resetSw();
  }
}

export function reportNetworkSuccess() {
  _consecutiveFailures = 0;
}

async function _resetSw() {
  if (sessionStorage.getItem("sw_reset_attempted")) return;
  let regs = [];
  try {
    regs = await navigator.serviceWorker.getRegistrations();
  } catch {}
  // Fetching the worker's own script bypasses the worker. If even that fails, the
  // server is what is unreachable, and a reset would leave nothing to reload from.
  try {
    await Promise.all(regs.map((r) => r.update()));
  } catch {
    _consecutiveFailures = 0;
    return;
  }
  sessionStorage.setItem("sw_reset_attempted", "1");
  try {
    await Promise.all(regs.map((r) => r.unregister()));
  } catch {}
  location.reload();
}

export function _resetForTest() {
  _consecutiveFailures = 0;
}
