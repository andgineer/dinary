import { ref } from "vue";

export function useSwipeRow({ panelWidth, commitOver = 80, onPrimary }) {
  const sliderEl = ref(null);
  const phase = ref("idle");
  const isCommit = ref(false);
  const isOpen = ref(false);

  const drag = {
    active: false,
    startX: 0,
    startY: 0,
    locked: null,
    visual: 0,
    moveDelta: 0,
    wasOpen: false,
  };

  function applyTransform(px) {
    if (sliderEl.value) {
      sliderEl.value.style.transform = px !== 0 ? `translateX(${px}px)` : "";
    }
    drag.visual = px;
  }

  function snapClose() {
    applyTransform(0);
    isOpen.value = false;
    phase.value = "idle";
  }

  function snapOpen() {
    applyTransform(-panelWidth);
    isOpen.value = true;
    phase.value = "open";
  }

  function onPointerDown(e) {
    sliderEl.value?.setPointerCapture?.(e.pointerId);
    drag.active = true;
    drag.startX = e.clientX;
    drag.startY = e.clientY;
    drag.locked = null;
    drag.moveDelta = 0;
    drag.wasOpen = isOpen.value;
    drag.visual = isOpen.value ? -panelWidth : 0;
  }

  function onPointerMove(e) {
    if (!drag.active) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;

    if (!drag.locked) {
      if (Math.abs(dx) > 8 || Math.abs(dy) > 8) {
        drag.locked = Math.abs(dx) >= Math.abs(dy) ? "h" : "v";
      }
      return;
    }

    if (drag.locked === "v") return;

    e.preventDefault();
    const base = isOpen.value ? -panelWidth : 0;
    const clamped = Math.max(-panelWidth, Math.min(0, base + dx));
    applyTransform(clamped);
    drag.moveDelta = Math.abs(dx);
    isCommit.value = Math.abs(clamped) >= commitOver;
    phase.value = "h";
  }

  function endDrag() {
    if (!drag.active) return;
    drag.active = false;
    const abs = Math.abs(drag.visual);
    if (!drag.wasOpen && abs >= commitOver) {
      onPrimary?.();
      snapClose();
    } else if (drag.wasOpen && drag.locked === null) {
      // tap on open panel (no axis committed) → close
      snapClose();
    } else if (abs > panelWidth / 2) {
      snapOpen();
    } else {
      snapClose();
    }
    isCommit.value = false;
  }

  function shouldFireTap() {
    return Math.abs(drag.visual) <= 4;
  }

  function close() {
    snapClose();
  }

  function open() {
    snapOpen();
  }

  return {
    sliderEl,
    phase,
    isCommit,
    isOpen,
    onPointerDown,
    onPointerMove,
    endDrag,
    shouldFireTap,
    close,
    open,
  };
}
