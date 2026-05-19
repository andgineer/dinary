import { describe, it, expect, vi } from "vitest";
import { useSwipeRow } from "../src/composables/useSwipeRow.js";

function mockEl() {
  return { style: { transform: "" }, setPointerCapture: vi.fn() };
}

function ptr(clientX, clientY) {
  return { clientX, clientY, pointerId: 1, preventDefault: vi.fn() };
}

describe("useSwipeRow — short swipe reveals panel", () => {
  it("snaps open when drag exceeds half panel width", () => {
    const { sliderEl, isOpen, onPointerDown, onPointerMove, endDrag } = useSwipeRow({
      panelWidth: 84,
      onPrimary: vi.fn(),
    });
    sliderEl.value = mockEl();

    onPointerDown(ptr(200, 50));
    onPointerMove(ptr(191, 51)); // 9px → locks to H
    onPointerMove(ptr(148, 51)); // 52px left (> half=42)
    endDrag();

    expect(isOpen.value).toBe(true);
  });
});

describe("useSwipeRow — long swipe fires onPrimary", () => {
  it("calls onPrimary and snaps closed when drag exceeds commitOver", () => {
    const onPrimary = vi.fn();
    const { sliderEl, isOpen, onPointerDown, onPointerMove, endDrag } = useSwipeRow({
      panelWidth: 84,
      commitOver: 80,
      onPrimary,
    });
    sliderEl.value = mockEl();

    onPointerDown(ptr(200, 50));
    onPointerMove(ptr(191, 51));
    onPointerMove(ptr(116, 51)); // 84px → past commitOver=80
    endDrag();

    expect(onPrimary).toHaveBeenCalledTimes(1);
    expect(isOpen.value).toBe(false);
  });
});

describe("useSwipeRow — vertical swipe does not transform slider", () => {
  it("does not move slider when gesture locks to vertical", () => {
    const { sliderEl, onPointerDown, onPointerMove, endDrag } = useSwipeRow({
      panelWidth: 84,
      onPrimary: vi.fn(),
    });
    const el = mockEl();
    sliderEl.value = el;

    onPointerDown(ptr(200, 50));
    onPointerMove(ptr(201, 62)); // mostly vertical (dy=12 > dx=1 → locks V)
    onPointerMove(ptr(202, 90));
    endDrag();

    expect(el.style.transform).toBe("");
  });
});

describe("useSwipeRow — open panel tap does not fire onPrimary", () => {
  it("does not call onPrimary when tapping slider while panel is already open", () => {
    const onPrimary = vi.fn();
    const { sliderEl, onPointerDown, onPointerMove, endDrag, open } = useSwipeRow({
      panelWidth: 84,
      commitOver: 80,
      onPrimary,
    });
    sliderEl.value = mockEl();

    open(); // snap panel open programmatically
    onPointerDown(ptr(100, 50)); // tap: no significant movement
    onPointerMove(ptr(102, 51)); // 2px — below axis-lock threshold
    endDrag();

    expect(onPrimary).not.toHaveBeenCalled();
  });

  it("snaps closed when tapping slider while panel is open", () => {
    const { sliderEl, isOpen, onPointerDown, onPointerMove, endDrag, open } = useSwipeRow({
      panelWidth: 84,
      commitOver: 80,
      onPrimary: vi.fn(),
    });
    sliderEl.value = mockEl();

    open();
    expect(isOpen.value).toBe(true);
    onPointerDown(ptr(100, 50));
    onPointerMove(ptr(102, 51));
    endDrag();

    expect(isOpen.value).toBe(false);
  });
});

describe("useSwipeRow — shouldFireTap", () => {
  it("returns true when movement is under 4px", () => {
    const { sliderEl, shouldFireTap, onPointerDown, onPointerMove } = useSwipeRow({
      panelWidth: 84,
      onPrimary: vi.fn(),
    });
    sliderEl.value = mockEl();

    onPointerDown(ptr(100, 50));
    onPointerMove(ptr(103, 51)); // 3px < 4 (but needs to lock first... won't lock at 3px)
    // No lock happens because neither dx nor dy exceeds 8px
    expect(shouldFireTap()).toBe(true);
  });

  it("returns false after a significant swipe", () => {
    const { sliderEl, shouldFireTap, onPointerDown, onPointerMove } = useSwipeRow({
      panelWidth: 84,
      onPrimary: vi.fn(),
    });
    sliderEl.value = mockEl();

    onPointerDown(ptr(200, 50));
    onPointerMove(ptr(191, 51));
    onPointerMove(ptr(160, 51)); // 40px swipe

    expect(shouldFireTap()).toBe(false);
  });
});
