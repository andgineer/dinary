<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";

const line = ref("");
let timer = null;

function safeArea(side) {
  const probe = document.createElement("div");
  probe.style.cssText = `position:fixed;left:-100px;width:1px;height:env(safe-area-inset-${side},0px);`;
  document.body.appendChild(probe);
  const value = Math.round(probe.getBoundingClientRect().height);
  probe.remove();
  return value;
}

function bottomOf(selector) {
  const el = document.querySelector(selector);
  return el ? Math.round(el.getBoundingClientRect().bottom) : "-";
}

function read() {
  const vv = window.visualViewport;
  if (!vv) {
    line.value = "no visualViewport";
    return;
  }
  const layoutH = document.documentElement.clientHeight || window.innerHeight;
  const scrollTop = window.scrollY || 0;
  const visibleTop = Number.isFinite(vv.pageTop) ? vv.pageTop : (vv.offsetTop || 0) + scrollTop;
  const covered = Math.round(scrollTop + layoutH - (visibleTop + vv.height));
  const edge = Math.round(visibleTop + vv.height - scrollTop);
  line.value = [
    `ih ${window.innerHeight}`,
    `ch ${layoutH}`,
    `vh ${Math.round(vv.height)}`,
    `off ${Math.round(vv.offsetTop)}`,
    `pt ${Math.round(vv.pageTop)}`,
    `sy ${Math.round(scrollTop)}`,
    `sat ${safeArea("top")}`,
    `sab ${safeArea("bottom")}`,
    `kb ${covered}`,
    `edge ${edge}`,
    `bar ${bottomOf(".kb-save-bar")}`,
    `act ${bottomOf(".action-bar")}`,
  ].join("  ");
}

onMounted(() => {
  read();
  timer = setInterval(read, 300);
  window.visualViewport?.addEventListener("resize", read);
  window.visualViewport?.addEventListener("scroll", read);
});

onBeforeUnmount(() => {
  clearInterval(timer);
  window.visualViewport?.removeEventListener("resize", read);
  window.visualViewport?.removeEventListener("scroll", read);
});
</script>

<template>
  <div class="kb-debug" data-testid="kb-debug">{{ line }}</div>
</template>

<style scoped>
.kb-debug {
  position: fixed;
  top: 36px;
  left: 0;
  right: 0;
  z-index: 30;
  padding: 3px 6px;
  background: rgba(0, 0, 0, 0.8);
  color: #4ade80;
  font-family: var(--font-num);
  font-size: 10px;
  line-height: 1.35;
  word-break: break-word;
  pointer-events: none;
}
</style>
