<script setup>
import {
  Plus, Settings, X, Pencil, Eye, EyeOff,
  Save, Search, Calendar, QrCode, Check, Hash, RefreshCw,
} from "lucide-vue-next";

const ICONS = {
  plus: Plus,
  cog: Settings,
  x: X,
  edit: Pencil,
  eye: Eye,
  "eye-off": EyeOff,
  save: Save,
  search: Search,
  cal: Calendar,
  scan: QrCode,
  check: Check,
  hash: Hash,
  refresh: RefreshCw,
};

const props = defineProps({
  icon: { type: String, required: true },
  tone: { type: String, default: "muted" },
  label: { type: String, required: true },
  disabled: { type: Boolean, default: false },
});

const emit = defineEmits(["click"]);
</script>

<template>
  <button
    type="button"
    class="icon-btn"
    :class="`icon-btn--${tone}`"
    :aria-label="label"
    :disabled="disabled"
    @click="emit('click')"
  >
    <component :is="ICONS[icon]" :size="14" aria-hidden="true" />
  </button>
</template>

<style scoped>
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 7px;
  border: none;
  background: transparent;
  cursor: pointer;
  flex-shrink: 0;
  padding: 0;
  color: var(--muted);
  transition: background 0.12s, color 0.12s;
}

.icon-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.icon-btn--accent { color: var(--accent); }
.icon-btn--danger { color: var(--danger); }
.icon-btn--muted  { color: var(--muted); }

.icon-btn--accent:not(:disabled):hover { background: rgba(233, 69, 96, 0.14); }
.icon-btn--danger:not(:disabled):hover { background: rgba(233, 69, 96, 0.14); }
.icon-btn--muted:not(:disabled):hover  { background: rgba(148, 163, 184, 0.14); }
</style>
