import { ref, onMounted, onBeforeUnmount } from "vue";

const KEYBOARD_THRESHOLD = 0.75;

export function useKeyboardVisible() {
  const keyboardVisible = ref(false);
  const keyboardBottom = ref(0);

  function update() {
    const vv = window.visualViewport;
    if (!vv) return;
    const ratio = vv.height / window.innerHeight;
    keyboardVisible.value = ratio < KEYBOARD_THRESHOLD;
    keyboardBottom.value = keyboardVisible.value
      ? window.innerHeight - vv.offsetTop - vv.height
      : 0;
  }

  onMounted(() => {
    if (!window.visualViewport) return;
    window.visualViewport.addEventListener("resize", update);
    window.visualViewport.addEventListener("scroll", update);
  });

  onBeforeUnmount(() => {
    if (!window.visualViewport) return;
    window.visualViewport.removeEventListener("resize", update);
    window.visualViewport.removeEventListener("scroll", update);
  });

  return { keyboardVisible, keyboardBottom };
}
