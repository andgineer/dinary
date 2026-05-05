import { defineStore } from "pinia";
import { ref } from "vue";

const SHORT_MS = 3000;
const LONG_MS = 8000;

export const useToastStore = defineStore("toast", () => {
  const message = ref("");
  const type = ref("info");
  const visible = ref(false);
  let _timer = null;

  function show(msg, t = "info") {
    if (_timer) {
      clearTimeout(_timer);
      _timer = null;
    }
    message.value = String(msg);
    type.value = t;
    visible.value = true;
    const delay = message.value.length > 60 ? LONG_MS : SHORT_MS;
    _timer = setTimeout(() => {
      visible.value = false;
      _timer = null;
    }, delay);
  }

  function hide() {
    if (_timer) {
      clearTimeout(_timer);
      _timer = null;
    }
    visible.value = false;
  }

  return { message, type, visible, show, hide };
});
