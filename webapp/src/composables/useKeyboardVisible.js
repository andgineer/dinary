import { ref, onMounted, onBeforeUnmount } from "vue";

const KEYBOARD_THRESHOLD = 0.75;
// iOS keeps animating the keyboard after its last visualViewport event, so a
// single reading taken on the event is often stale.
const REMEASURE_DELAYS_MS = [50, 250, 600];

export function useKeyboardVisible() {
  const keyboardVisible = ref(false);
  const keyboardBottom = ref(0);
  let timers = [];

  function clearTimers() {
    timers.forEach(clearTimeout);
    timers = [];
  }

  function update() {
    const vv = window.visualViewport;
    if (!vv) return;
    // `position: fixed` resolves against the layout viewport, whose height is
    // documentElement.clientHeight — window.innerHeight can be larger, e.g. an
    // iOS home-screen app drawing under the status bar.
    const layoutHeight = document.documentElement.clientHeight || window.innerHeight;
    const scrollTop = window.scrollY || 0;
    const visibleTop = Number.isFinite(vv.pageTop)
      ? vv.pageTop
      : (vv.offsetTop || 0) + scrollTop;

    keyboardVisible.value = vv.height / layoutHeight < KEYBOARD_THRESHOLD;
    // Both edges in document coordinates: what the fixed layer extends past
    // the visible area is exactly what the keyboard covers.
    const covered = scrollTop + layoutHeight - (visibleTop + vv.height);
    keyboardBottom.value = keyboardVisible.value ? Math.max(0, Math.round(covered)) : 0;
    // iOS can leave the document scrolled once the keyboard closes, which
    // parks fixed bottom bars below the screen until the next scroll.
    if (!keyboardVisible.value && scrollTop) window.scrollTo(0, 0);
  }

  function remeasure() {
    clearTimers();
    timers = REMEASURE_DELAYS_MS.map((ms) => setTimeout(update, ms));
  }

  onMounted(() => {
    if (!window.visualViewport) return;
    window.visualViewport.addEventListener("resize", update);
    window.visualViewport.addEventListener("scroll", update);
    window.addEventListener("orientationchange", remeasure);
    document.addEventListener("focusin", remeasure);
    document.addEventListener("focusout", remeasure);
  });

  onBeforeUnmount(() => {
    clearTimers();
    if (!window.visualViewport) return;
    window.visualViewport.removeEventListener("resize", update);
    window.visualViewport.removeEventListener("scroll", update);
    window.removeEventListener("orientationchange", remeasure);
    document.removeEventListener("focusin", remeasure);
    document.removeEventListener("focusout", remeasure);
  });

  return { keyboardVisible, keyboardBottom };
}
