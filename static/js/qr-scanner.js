/**
 * QR scanner using html5-qrcode (loaded from CDN).
 * Opens the rear camera, scans for a URL, calls the callback with the result.
 */

let _scanner = null;

export async function startScanning(readerId, onResult) {
  if (typeof Html5Qrcode === "undefined") {
    throw new Error("html5-qrcode library not loaded");
  }

  stop();

  _scanner = new Html5Qrcode(readerId);
  await _scanner.start(
    { facingMode: "environment" },
    { fps: 10, qrbox: { width: 250, height: 250 } },
    (text) => {
      stop();
      onResult(text);
    },
    () => {},
  );
}

export function stop() {
  if (_scanner && _scanner.isScanning) {
    _scanner.stop().catch(() => {});
    _scanner = null;
  }
}

export function isScanning() {
  return _scanner !== null && _scanner.isScanning;
}
