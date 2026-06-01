<script setup>
import { computed, ref, watch } from "vue";
import { X, Eye, EyeOff, Trash2 } from "lucide-vue-next";
import { useLlmStore } from "../stores/llm.js";

const props = defineProps({
  open: { type: Boolean, default: false },
  provider: { type: Object, default: null },
});
const emit = defineEmits(["close"]);

const llmStore = useLlmStore();

const PRESETS = {
  Groq: {
    base_url: "https://api.groq.com/openai/v1",
    models: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "gemma2-9b-it"],
  },
  OpenRouter: {
    base_url: "https://openrouter.ai/api/v1",
    models: [
      "nvidia/llama-3.1-nemotron-70b-instruct",
      "nousresearch/hermes-3-llama-3.1-405b",
      "anthropic/claude-3.5-sonnet",
    ],
  },
  Gemini: {
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    models: ["gemini-1.5-flash-latest", "gemini-1.5-pro-latest"],
  },
  Custom: {
    base_url: "",
    models: [],
  },
};

const isEditMode = computed(() => !!props.provider);

const parsedError = computed(() => {
  const raw = props.provider?.last_error_detail;
  if (!raw) return null;
  try {
    let parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) parsed = parsed[0];
    return parsed?.error?.message ?? raw;
  } catch {
    return raw;
  }
});

const label = ref("");
const baseUrl = ref("");
const model = ref("");
const apiKey = ref("");
const isEnabled = ref(true);
const selectedPreset = ref(null);
const showApiKey = ref(false);
const submitting = ref(false);
const confirmingDelete = ref(false);

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;

    confirmingDelete.value = false;
    showApiKey.value = false;
    submitting.value = false;
    if (props.provider) {
      label.value = props.provider.label ?? "";
      baseUrl.value = props.provider.base_url ?? "";
      model.value = props.provider.model ?? "";
      apiKey.value = "";
      isEnabled.value = props.provider.is_enabled ?? true;
      selectedPreset.value = detectPreset(props.provider.base_url);
    } else {
      label.value = "";
      baseUrl.value = "";
      model.value = "";
      apiKey.value = "";
      isEnabled.value = true;
      selectedPreset.value = null;
    }
  },
  { immediate: true },
);

function detectPreset(url) {
  for (const [name, preset] of Object.entries(PRESETS)) {
    if (name !== "Custom" && url && url.startsWith(preset.base_url)) return name;
  }
  return null;
}

function applyPreset(name) {
  selectedPreset.value = name;
  const preset = PRESETS[name];
  if (preset) {
    baseUrl.value = preset.base_url;
    if (preset.models.length > 0 && !model.value) {
      model.value = preset.models[0];
    }
  }
}

const modelSuggestions = computed(() => {
  if (!selectedPreset.value) return [];
  return PRESETS[selectedPreset.value]?.models ?? [];
});

const canSubmit = computed(() => {
  const hasRequiredFields = label.value.trim() && baseUrl.value.trim() && model.value.trim();
  if (isEditMode.value) return hasRequiredFields;
  return hasRequiredFields && apiKey.value.trim();
});

async function submit() {
  if (!canSubmit.value) return;
  submitting.value = true;
  try {
    const payload = {
      label: label.value.trim(),
      base_url: baseUrl.value.trim(),
      model: model.value.trim(),
      is_enabled: isEnabled.value,
    };
    if (apiKey.value.trim()) payload.api_key = apiKey.value.trim();
    if (isEditMode.value) payload.id = props.provider.id;
    await llmStore.save(payload);
    emit("close");
  } catch {
    // toast already shown by store
  } finally {
    submitting.value = false;
  }
}

async function handleDelete() {
  if (!confirmingDelete.value) {
    confirmingDelete.value = true;
    return;
  }
  submitting.value = true;
  try {
    await llmStore.remove(props.provider.id);
    emit("close");
  } catch {
    // toast shown by store
  } finally {
    submitting.value = false;
    confirmingDelete.value = false;
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="scrim">
      <div v-if="open" class="sheet-scrim" @click="emit('close')" />
    </Transition>
    <Transition name="sheet">
    <div
      v-if="open"
      class="sheet"
      role="dialog"
      aria-modal="true"
      :aria-label="isEditMode ? 'Edit provider' : 'Add provider'"
      data-testid="provider-sheet"
    >
      <div class="drag-handle" />

      <div class="sheet-header">
        <div class="sheet-eyebrow">{{ isEditMode ? "EDIT PROVIDER" : "ADD PROVIDER" }}</div>
        <div class="sheet-title">
          {{ isEditMode ? `${provider.label} · ${provider.model}` : "New entry" }}
        </div>
        <button type="button" class="sheet-close" aria-label="Close" @click="emit('close')">
          <X :size="16" />
        </button>
      </div>

      <div class="sheet-body">
        <div v-if="parsedError" class="error-banner">{{ parsedError }}</div>

        <div class="preset-row">
          <button
            v-for="(_, name) in PRESETS"
            :key="name"
            type="button"
            class="preset-chip"
            :class="{ 'is-active': selectedPreset === name }"
            @click="applyPreset(name)"
          >
            {{ name }}
          </button>
        </div>

        <div class="field-group">
          <label for="ps-label">LABEL</label>
          <input id="ps-label" v-model="label" type="text" autocomplete="off" placeholder="e.g. Groq" />
        </div>

        <div class="field-group">
          <label for="ps-base-url">BASE URL</label>
          <input id="ps-base-url" v-model="baseUrl" type="text" autocomplete="off" class="mono" />
        </div>

        <div class="field-group">
          <label for="ps-model">MODEL</label>
          <input id="ps-model" v-model="model" type="text" autocomplete="off" class="mono" />
          <div v-if="modelSuggestions.length > 0" class="model-suggestions">
            <button
              v-for="m in modelSuggestions"
              :key="m"
              type="button"
              class="model-chip"
              :class="{ 'is-active': model === m }"
              @click="model = m"
            >
              {{ m }}
            </button>
          </div>
        </div>

        <div class="field-group">
          <label for="ps-api-key">API KEY</label>
          <div class="key-row">
            <input
              id="ps-api-key"
              v-model="apiKey"
              :type="showApiKey ? 'text' : 'password'"
              :placeholder="isEditMode ? 'Leave empty to keep existing key' : ''"
              autocomplete="new-password"
              class="mono key-input"
            />
            <button
              type="button"
              class="eye-btn"
              :aria-label="showApiKey ? 'Hide key' : 'Show key'"
              @click="showApiKey = !showApiKey"
            >
              <EyeOff v-if="showApiKey" :size="15" />
              <Eye v-else :size="15" />
            </button>
          </div>
        </div>

        <div class="enabled-row">
          <label class="toggle-label">
            <input v-model="isEnabled" type="checkbox" />
            <span>Enabled in failover pool</span>
          </label>
        </div>

        <template v-if="isEditMode">
          <div class="delete-separator" />
          <div v-if="!confirmingDelete">
            <button type="button" class="btn-delete" @click="handleDelete">
              <Trash2 :size="14" />
              Remove provider
            </button>
          </div>
          <div v-else class="delete-confirm">
            <p class="delete-text">
              Remove {{ provider.label }} from the failover pool? Existing call logs are kept.
            </p>
            <div class="delete-actions">
              <button type="button" class="btn-cancel-delete" @click="confirmingDelete = false">
                Cancel
              </button>
              <button
                type="button"
                class="btn-confirm-delete"
                :disabled="submitting"
                @click="handleDelete"
              >
                <Trash2 :size="13" />
                Remove
              </button>
            </div>
          </div>
        </template>
      </div>

      <div class="sheet-footer">
        <button type="button" class="btn-ghost" @click="emit('close')">Cancel</button>
        <button
          type="button"
          class="btn btn-primary submit-btn"
          :disabled="!canSubmit || submitting"
          @click="submit"
        >
          {{ isEditMode ? "✓ Save changes" : "✓ Add provider" }}
        </button>
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
  z-index: 40;
}

.sheet {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 45;
  background: var(--surface);
  border-radius: 18px 18px 0 0;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 -4px 24px rgba(0, 0, 0, 0.35);
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

.sheet-eyebrow {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--muted);
  text-transform: uppercase;
}

.sheet-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--text);
  margin-top: 2px;
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

.error-banner {
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 8px;
  padding: 0.55rem 0.75rem;
  font-size: 0.82rem;
  color: var(--danger, #ef4444);
  margin-bottom: 0.75rem;
  word-break: break-word;
}

.preset-row {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
  margin-bottom: 1rem;
}

.preset-chip {
  padding: 0.3rem 0.7rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--field);
  color: var(--muted);
  font-size: 0.8rem;
  cursor: pointer;
  width: auto;
  transition: border-color 0.12s, color 0.12s;
}

.preset-chip.is-active {
  border-color: var(--accent);
  color: var(--accent);
  background: rgba(91, 141, 239, 0.08);
}

.field-group {
  margin-bottom: 0.75rem;
}

.field-group label {
  font-size: 0.65rem;
  letter-spacing: 0.07em;
  color: var(--muted);
  margin-bottom: 0.25rem;
}

.mono {
  font-family: var(--font-num);
  font-size: 0.85rem;
}

.key-row {
  position: relative;
  display: flex;
  align-items: center;
}

.key-input {
  flex: 1;
  padding-right: 2.5rem;
}

.eye-btn {
  position: absolute;
  right: 0.5rem;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  padding: 0.25rem;
  width: auto;
  display: flex;
  align-items: center;
}

.model-suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-top: 0.4rem;
}

.model-chip {
  padding: 0.2rem 0.55rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--field-deep);
  color: var(--muted);
  font-family: var(--font-num);
  font-size: 0.72rem;
  cursor: pointer;
  width: auto;
  transition: border-color 0.12s;
}

.model-chip.is-active {
  border-color: var(--accent);
  color: var(--accent);
}

.enabled-row {
  margin: 0.5rem 0;
}

.toggle-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  text-transform: none;
  letter-spacing: 0;
  color: var(--text);
  font-size: 0.85rem;
  cursor: pointer;
  margin-bottom: 0;
}

.toggle-label input[type="checkbox"] {
  width: auto;
}

.delete-separator {
  border: none;
  border-top: 1px dashed var(--border-strong);
  margin: 0.75rem 0;
}

.btn-delete {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  background: none;
  border: 1px solid var(--border);
  color: var(--error);
  font-size: 0.85rem;
  border-radius: 8px;
  padding: 0.4rem 0.75rem;
  cursor: pointer;
  width: auto;
  transition: border-color 0.12s, background 0.12s;
}

.btn-delete:hover {
  border-color: var(--error);
  background: rgba(239, 68, 68, 0.08);
}

.delete-confirm {
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 8px;
  padding: 0.65rem 0.75rem;
}

.delete-text {
  font-size: 0.82rem;
  color: var(--text);
  margin-bottom: 0.5rem;
}

.delete-actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
}

.btn-cancel-delete {
  background: none;
  border: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.82rem;
  border-radius: 6px;
  padding: 0.3rem 0.65rem;
  cursor: pointer;
  width: auto;
}

.btn-confirm-delete {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  background: var(--error);
  border: none;
  color: #fff;
  font-size: 0.82rem;
  font-weight: 600;
  border-radius: 6px;
  padding: 0.3rem 0.65rem;
  cursor: pointer;
  width: auto;
}

.btn-confirm-delete:disabled {
  opacity: 0.5;
}

.sheet-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem calc(0.75rem + env(safe-area-inset-bottom, 0px));
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

.btn-ghost {
  background: none;
  border: none;
  color: var(--muted);
  font-size: 0.9rem;
  cursor: pointer;
  padding: 0.5rem 0.75rem;
  width: auto;
  border-radius: 8px;
  transition: background 0.12s;
}

.btn-ghost:hover {
  background: var(--field);
}

.submit-btn {
  width: auto;
  padding: 0.5rem 1.25rem;
  font-size: 0.9rem;
}
</style>
