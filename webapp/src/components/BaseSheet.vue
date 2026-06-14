<script setup>
import { computed } from "vue";
import { X } from "lucide-vue-next";
import { useKeyboardVisible } from "../composables/useKeyboardVisible.js";

defineOptions({ inheritAttrs: false });

const props = defineProps({
  open: { type: Boolean, default: false },
  dimmed: { type: Boolean, default: false },
  ariaLabel: { type: String, default: "Sheet" },
  tall: { type: Boolean, default: false },
  fullHeight: { type: Boolean, default: false },
  zIndex: { type: Number, default: 45 },
});
const emit = defineEmits(["close"]);

// On-screen keyboards eat into the visual viewport without shrinking the
// layout viewport that `position: fixed` and `vh` units are based on. Pull
// the sheet's bottom edge up by the keyboard's height so the footer (and,
// for tall sheets, the whole sheet) stays within the visible area.
const { keyboardBottom } = useKeyboardVisible();

const sheetStyle = computed(() => {
  const kb = keyboardBottom.value;
  const style = { zIndex: props.zIndex, bottom: `${kb}px` };
  if (kb > 0 && !props.fullHeight) {
    style.maxHeight = `calc(80vh - ${kb}px)`;
    if (props.tall) {
      style.minHeight = `min(50vh, calc(100vh - ${kb}px))`;
    }
  }
  return style;
});
</script>

<template>
  <Teleport to="body">
    <Transition name="scrim">
      <div v-if="open" class="sheet-scrim" :style="{ zIndex: zIndex - 5 }" @click="emit('close')" />
    </Transition>
    <Transition name="sheet">
      <div
        v-if="open"
        class="sheet"
        :class="{ 'sheet-dimmed': dimmed, 'sheet-full': fullHeight, 'sheet-tall': tall }"
        :style="sheetStyle"
        v-bind="$attrs"
        role="dialog"
        aria-modal="true"
        :aria-label="ariaLabel"
      >
        <div class="drag-handle" />
        <div class="sheet-header">
          <slot name="header" />
          <button type="button" class="sheet-close" aria-label="Close" @click="emit('close')">
            <X :size="16" />
          </button>
        </div>
        <slot name="pre-body" />
        <div class="sheet-body">
          <slot />
        </div>
        <div v-if="$slots.footer" class="sheet-footer">
          <slot name="footer" />
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim-enter-active,
.scrim-leave-active {
  transition: opacity 0.26s;
}
.scrim-enter-from,
.scrim-leave-to {
  opacity: 0;
}

.sheet-enter-active,
.sheet-leave-active {
  transition: transform 0.28s cubic-bezier(0.32, 0, 0.67, 0);
}
.sheet-enter-from,
.sheet-leave-to {
  transform: translateY(100%);
}

.sheet-scrim {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
}

.sheet {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  background: var(--surface);
  border-radius: 18px 18px 0 0;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 -4px 24px rgba(0, 0, 0, 0.35);
  transition: opacity 0.18s, filter 0.18s;
}

.sheet-full {
  top: 0;
  max-height: none;
  overscroll-behavior: contain;
}

.sheet-tall {
  min-height: 50vh;
}

.sheet-dimmed {
  opacity: 0.55;
  filter: blur(0.5px);
  pointer-events: none;
}

.drag-handle {
  width: 36px;
  height: 4px;
  border-radius: 2px;
  background: var(--border-strong);
  margin: 10px auto 0;
  flex-shrink: 0;
}

.sheet-header {
  padding: 0.75rem 3rem 0.5rem 1rem;
  position: relative;
  flex-shrink: 0;
}

.sheet-close {
  position: absolute;
  top: 0.75rem;
  right: 1rem;
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  padding: 0.25rem;
  width: auto;
  display: flex;
  align-items: center;
}

.sheet-body {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 1rem;
}

.sheet-footer {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem calc(0.75rem + env(safe-area-inset-bottom, 0px));
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

:slotted(.sheet-eyebrow) {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--muted);
  text-transform: uppercase;
}
</style>
