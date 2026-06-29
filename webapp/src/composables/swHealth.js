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
  sessionStorage.setItem("sw_reset_attempted", "1");
  try {
    const regs = await navigator.serviceWorker.getRegistrations();
    await Promise.all(regs.map((r) => r.unregister()));
  } catch {}
  location.reload();
}

export function _resetForTest() {
  _consecutiveFailures = 0;
}
