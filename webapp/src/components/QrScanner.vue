<script setup>
import { onBeforeUnmount, ref } from "vue";

const emit = defineEmits(["scan", "error"]);
defineExpose({ start, stop, isActive });

const videoEl = ref(null);
const active = ref(false);

let _stream = null;
let _animId = null;
let _zbar = null;

async function loadZbar() {
  if (_zbar) return _zbar;
  // Loaded from CDN at runtime so vite-plugin-pwa doesn't try to
  // precache the wasm bundle. The scanner is opt-in (only loads when
  // the user taps the QR button) so the lazy import is correct.
  _zbar = await import(
    /* @vite-ignore */
    "https://cdn.jsdelivr.net/npm/@undecaf/zbar-wasm@0.11.0/dist/inlined/index.mjs"
  );
  return _zbar;
}

async function applyZoomFocus() {
  const track = videoEl.value?.srcObject?.getVideoTracks?.()?.[0];
  if (!track) return;
  const caps = track.getCapabilities?.();
  if (!caps) return;
  const advanced = [];
  if (caps.zoom) advanced.push({ zoom: Math.min(2.0, caps.zoom.max) });
  if (caps.focusDistance) advanced.push({ focusDistance: caps.focusDistance.min });
  if (advanced.length > 0) {
    try {
      await track.applyConstraints({ advanced });
    } catch {
      // Some browsers reject the advanced block silently; not fatal.
    }
  }
}

function scanLoop(canvas, ctx) {
  if (!_stream || !videoEl.value) return;
  const w = videoEl.value.videoWidth;
  const h = videoEl.value.videoHeight;
  if (w && h) {
    canvas.width = w;
    canvas.height = h;
    ctx.drawImage(videoEl.value, 0, 0, w, h);
    const imageData = ctx.getImageData(0, 0, w, h);
    _zbar.scanImageData(imageData).then((symbols) => {
      if (!_stream) return;
      if (symbols.length > 0) {
        const text = symbols[0].decode("utf-8");
        const result = text;
        stop();
        emit("scan", result);
        return;
      }
      _animId = requestAnimationFrame(() => scanLoop(canvas, ctx));
    });
  } else {
    _animId = requestAnimationFrame(() => scanLoop(canvas, ctx));
  }
}

async function start() {
  if (!navigator.mediaDevices?.getUserMedia) {
    const err = new Error("Camera requires HTTPS");
    emit("error", err);
    throw err;
  }
  stop();
  await loadZbar();
  _stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: {
      facingMode: "environment",
      width: { ideal: 1920 },
      height: { ideal: 1080 },
    },
  });
  videoEl.value.srcObject = _stream;
  await videoEl.value.play();
  setTimeout(applyZoomFocus, 500);
  active.value = true;
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  _animId = requestAnimationFrame(() => scanLoop(canvas, ctx));
}

function stop() {
  if (_animId) {
    cancelAnimationFrame(_animId);
    _animId = null;
  }
  if (_stream) {
    _stream.getTracks().forEach((t) => t.stop());
    _stream = null;
  }
  active.value = false;
}

function isActive() {
  return active.value;
}

onBeforeUnmount(stop);
</script>

<template>
  <video
    ref="videoEl"
    class="qr-video"
    :class="{ 'is-hidden': !active }"
    aria-label="QR scanner preview"
  />
</template>

<style scoped>
.qr-video {
  width: 100%;
  border-radius: 8px;
  margin-bottom: 1rem;
  display: block;
}
.is-hidden {
  display: none;
}
</style>
