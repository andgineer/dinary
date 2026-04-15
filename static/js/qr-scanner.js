/**
 * QR scanner using html5-qrcode (loaded from CDN).
 * Opens the rear camera, scans for a URL, calls the callback with the result.
 */

let _scanner = null;

export async function startScanning(readerId, onResult) {
  if (typeof Html5Qrcode === "undefined") {
    throw new Error("html5-qrcode library not loaded");
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("Camera requires HTTPS — open via https:// or localhost");
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true });
    stream.getTracks().forEach((t) => t.stop());
  } catch (e) {
    if (e.name === "NotAllowedError") {
      throw new Error("Camera blocked — allow in browser Settings → Site Settings");
    }
    if (e.name === "NotFoundError") {
      throw new Error("No camera found on this device");
    }
    throw new Error(`Camera: ${e.message}`);
  }

  stop();

  _scanner = new Html5Qrcode(readerId);
  await _scanner.start(
    { facingMode: "environment" },
    {
      fps: 10,
      qrbox: (vw, vh) => {
        const side = Math.floor(Math.min(vw, vh) * 0.8);
        return { width: side, height: side };
      },
    },
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
