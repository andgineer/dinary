/**
 * QR scanner using nimiq/qr-scanner — ZXing-based with WebWorker,
 * 2-3x better detection than html5-qrcode.
 * Full-resolution scan region (no downscaling) for dense QR codes.
 */

import QrScanner from "./qr-scanner-lib.js";

let _scanner = null;

export async function startScanning(videoElem, onResult) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("Camera requires HTTPS");
  }

  stop();

  _scanner = new QrScanner(
    videoElem,
    (result) => {
      stop();
      onResult(result.data);
    },
    {
      preferredCamera: "environment",
      maxScansPerSecond: 25,
      highlightScanRegion: true,
      highlightCodeOutline: true,
      returnDetailedScanResult: true,
      calculateScanRegion: (video) => ({
        x: 0,
        y: 0,
        width: video.videoWidth,
        height: video.videoHeight,
      }),
    },
  );
  await _scanner.start();
}

export function stop() {
  if (_scanner) {
    _scanner.destroy();
    _scanner = null;
  }
}
