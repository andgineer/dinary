/**
 * QR scanner using zbar-wasm (ZBar C library compiled to WebAssembly).
 * ZBar handles dense QR codes much better than ZXing-based JS decoders.
 *
 * iOS workaround: apply zoom + focusDistance constraints to compensate
 * for Safari's 720p getUserMedia limit.
 */

let _stream = null;
let _animId = null;
let _onResult = null;
let _zbar = null;

async function _loadZbar() {
  if (_zbar) return _zbar;
  _zbar = await import(
    "https://cdn.jsdelivr.net/npm/@undecaf/zbar-wasm@0.11.0/dist/inlined/index.mjs"
  );
  return _zbar;
}

async function _applyZoomFocus(videoElem) {
  const track = videoElem.srcObject?.getVideoTracks()?.[0];
  if (!track) return;

  const caps = track.getCapabilities?.();
  if (!caps) return;

  const advanced = [];
  if (caps.zoom) {
    advanced.push({ zoom: Math.min(2.0, caps.zoom.max) });
  }
  if (caps.focusDistance) {
    advanced.push({ focusDistance: caps.focusDistance.min });
  }
  if (advanced.length > 0) {
    await track.applyConstraints({ advanced });
  }
}

function _scanLoop(videoElem, canvas, ctx) {
  if (!_stream) return;

  const w = videoElem.videoWidth;
  const h = videoElem.videoHeight;

  if (w && h) {
    canvas.width = w;
    canvas.height = h;
    ctx.drawImage(videoElem, 0, 0, w, h);
    const imageData = ctx.getImageData(0, 0, w, h);

    _zbar.scanImageData(imageData).then((symbols) => {
      if (symbols.length > 0 && _onResult) {
        const text = symbols[0].decode("utf-8");
        const cb = _onResult;
        stop();
        cb(text);
        return;
      }
      _animId = requestAnimationFrame(() => _scanLoop(videoElem, canvas, ctx));
    });
  } else {
    _animId = requestAnimationFrame(() => _scanLoop(videoElem, canvas, ctx));
  }
}

export async function startScanning(videoElem, onResult) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("Camera requires HTTPS");
  }

  stop();

  await _loadZbar();

  _stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: {
      facingMode: "environment",
      width: { ideal: 1920 },
      height: { ideal: 1080 },
    },
  });

  videoElem.srcObject = _stream;
  await videoElem.play();

  setTimeout(() => _applyZoomFocus(videoElem), 500);

  _onResult = onResult;

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  _animId = requestAnimationFrame(() => _scanLoop(videoElem, canvas, ctx));
}

export function stop() {
  if (_animId) {
    cancelAnimationFrame(_animId);
    _animId = null;
  }
  _onResult = null;
  if (_stream) {
    _stream.getTracks().forEach((t) => t.stop());
    _stream = null;
  }
}
