import { onBeforeUnmount, ref } from "vue";

export function useNow(intervalMs = 30_000) {
  const now = ref(Date.now());
  const timer = setInterval(() => {
    now.value = Date.now();
  }, intervalMs);

  onBeforeUnmount(() => {
    clearInterval(timer);
  });

  return now;
}
