<script setup>
import { Save } from "lucide-vue-next";

const props = defineProps({
  bottom: { type: Number, default: 0 },
});

const emit = defineEmits(["save"]);
</script>

<template>
  <div class="kb-save-bar" :style="{ bottom: props.bottom + 'px' }">
    <button type="button" class="kb-save-btn" @click="emit('save')">
      <Save :size="18" aria-hidden="true" />
      Save
    </button>
  </div>
</template>

<style scoped>
.kb-save-bar {
  position: fixed;
  left: 0;
  right: 0;
  z-index: 20;
  padding: 6px 12px;
  background: var(--surface);
  border-top: 1px solid var(--border, rgba(255, 255, 255, 0.08));
  box-shadow: 0 -2px 10px rgba(0, 0, 0, 0.2);
  display: flex;
  justify-content: center;
  animation: slide-up 0.15s ease-out;
}

.kb-save-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 40px;
  padding: 0 2rem;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 10px;
  font-size: 1rem;
  font-weight: 600;
  cursor: pointer;
  width: 100%;
  max-width: 480px;
  justify-content: center;
}

@keyframes slide-up {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
