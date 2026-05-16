import { onBeforeUnmount, onMounted, ref } from "vue";

export function useOnline() {
  const isOnline = ref(typeof navigator !== "undefined" ? navigator.onLine : true);

  function setOnline() { isOnline.value = true; }
  function setOffline() { isOnline.value = false; }

  onMounted(() => {
    window.addEventListener("online", setOnline);
    window.addEventListener("offline", setOffline);
  });

  onBeforeUnmount(() => {
    window.removeEventListener("online", setOnline);
    window.removeEventListener("offline", setOffline);
  });

  return { isOnline };
}
