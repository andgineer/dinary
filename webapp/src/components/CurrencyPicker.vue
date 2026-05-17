<script setup>
import { computed, ref, watch } from "vue";
import { useCurrencyStore } from "../stores/currency.js";
import { useToastStore } from "../stores/toast.js";
import { WORLD_CURRENCIES } from "../data/world-currencies.js";
import IconBtn from "./IconBtn.vue";

const props = defineProps({
  modelValue: { type: String, default: "" },
});
const emit = defineEmits(["update:modelValue", "close"]);

const currency = useCurrencyStore();
const toast = useToastStore();

const manageMode = ref(false);
const search = ref("");
const pendingCode = ref(null);

const value = computed({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
});

const savedCodes = computed(() => currency.codes);

const filteredWorld = computed(() => {
  const q = search.value.trim().toUpperCase();
  if (!q) return [];
  // Hide currencies the operator already has saved — adding them
  // again would be a no-op and visually noisy.
  const taken = new Set(savedCodes.value);
  // Match against ISO code, English name AND common symbols / shorthand.
  // The third field is what makes "KM" find BAM, "kr" return all the
  // Nordics, "Kč" find CZK, etc., without the operator having to know
  // ISO codes.
  return WORLD_CURRENCIES.filter((c) => {
    if (taken.has(c.code)) return false;
    if (c.code.includes(q)) return true;
    if (c.name.toUpperCase().includes(q)) return true;
    return (c.symbols || []).some((s) => s.toUpperCase().includes(q));
  }).slice(0, 12);
});

function onSelect(code) {
  value.value = code;
  currency.setLastUsed(code);
  emit("close");
}

async function onAdd(code) {
  if (pendingCode.value) return;
  pendingCode.value = code;
  try {
    await currency.addCurrency(code);
    search.value = "";
    onSelect(code);
  } catch (err) {
    toast.show(`Add failed: ${err?.message || err}`, "error");
  } finally {
    pendingCode.value = null;
  }
}

async function onRemove(code) {
  if (pendingCode.value) return;
  if (code === currency.defaultCode) {
    toast.show("Cannot delete the default currency", "error");
    return;
  }
  if (!window.confirm(`Remove ${code} from the picker?`)) return;
  pendingCode.value = code;
  try {
    await currency.removeCurrency(code);
    if (value.value === code) {
      onSelect(currency.preferredCode);
    }
  } catch (err) {
    toast.show(`Delete failed: ${err?.message || err}`, "error");
  } finally {
    pendingCode.value = null;
  }
}

function toggleManage() {
  manageMode.value = !manageMode.value;
  if (!manageMode.value) search.value = "";
}

watch(
  () => currency.codes,
  () => {
    // If the parent's modelValue is no longer a saved code (e.g.
    // initial load before defaults arrive), nudge it onto the
    // store's preferred code so the form's currency label stays
    // truthful.
    if (value.value && !savedCodes.value.includes(value.value)) {
      value.value = currency.preferredCode;
    }
  },
);
</script>

<template>
  <div class="currency-picker" data-testid="currency-picker">
    <div class="currency-row currency-row-saved">
      <button
        v-for="code in savedCodes"
        :key="code"
        type="button"
        class="currency-chip"
        :class="{ 'currency-chip-selected': value === code }"
        :disabled="pendingCode === code"
        @click="onSelect(code)"
      >
        <span class="currency-code">{{ code }}</span>
      </button>
      <IconBtn
        :icon="manageMode ? 'x' : 'cog'"
        tone="muted"
        :label="manageMode ? 'Close' : 'Manage currencies'"
        class="currency-manage"
        @click="toggleManage"
      />
    </div>

    <div v-if="manageMode" class="currency-manage-panel">
      <input
        v-model="search"
        type="text"
        placeholder="Search ISO code or name…"
        autocomplete="off"
        aria-label="Currency search"
      />
      <div v-if="filteredWorld.length > 0" class="currency-world-list">
        <button
          v-for="c in filteredWorld"
          :key="c.code"
          type="button"
          class="currency-world-row"
          :disabled="pendingCode === c.code"
          @click="onAdd(c.code)"
        >
          <span class="currency-code">{{ c.code }}</span>
          <span class="currency-name">{{ c.name }}</span>
        </button>
      </div>
      <div v-else-if="search.trim()" class="currency-empty">
        No matches
      </div>

      <div v-if="savedCodes.length > 0" class="currency-saved-list">
        <div class="currency-saved-header">Saved (click to remove)</div>
        <div
          v-for="code in savedCodes"
          :key="`saved-${code}`"
          class="currency-saved-row"
        >
          <span class="currency-code">{{ code }}</span>
          <button
            type="button"
            class="btn-inline currency-remove"
            :disabled="
              pendingCode === code || code === currency.defaultCode
            "
            :title="
              code === currency.defaultCode
                ? 'Default currency — cannot remove'
                : 'Remove'
            "
            @click="onRemove(code)"
          >
            ×
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.currency-picker {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.currency-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  align-items: center;
}

.currency-chip {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  background: var(--surface-2);
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 0.25rem 0.55rem;
  font-size: 0.75rem;
  color: var(--text);
  cursor: pointer;
  user-select: none;
  width: auto;
  margin-bottom: 0;
}

.currency-chip-selected {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}

.currency-code {
  font-weight: 600;
  letter-spacing: 0.04em;
}

.currency-manage {
  margin-left: auto;
}

.currency-manage-panel {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  background: var(--bg);
  border: 1px dashed var(--surface-2);
  border-radius: 8px;
  padding: 0.5rem;
}

.currency-world-list {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.currency-world-row {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  background: transparent;
  border: 1px solid var(--surface-2);
  border-radius: 6px;
  padding: 0.25rem 0.4rem;
  font-size: 0.78rem;
  color: var(--text);
  cursor: pointer;
  text-align: left;
  width: 100%;
  margin-bottom: 0;
}

.currency-name {
  color: var(--text-muted);
  font-size: 0.72rem;
}

.currency-empty {
  font-size: 0.75rem;
  color: var(--text-muted);
  font-style: italic;
}

.currency-saved-list {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  margin-top: 0.25rem;
}

.currency-saved-header {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-muted);
}

.currency-saved-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.78rem;
}

.currency-saved-row .currency-remove {
  margin-left: auto;
}

.currency-remove {
  font-size: 0.7rem;
  color: #fca5a5;
  background: transparent;
  border: 1px solid #7f1d1d;
}

.currency-remove:hover:not(:disabled) {
  background: #7f1d1d;
  color: #fee2e2;
}
</style>
