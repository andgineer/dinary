<script setup>
import { computed } from "vue";
import { Power } from "lucide-vue-next";
import StatusDot from "./StatusDot.vue";

const props = defineProps({
  provider: { type: Object, required: true },
  now: { type: Number, default: () => Date.now() },
});
const emit = defineEmits(["toggle"]);

const STATUS_LABELS = {
  available: "available",
  cooling: "cooling down",
  no_key: "no key",
  disabled: "disabled",
};

const STATUS_DOTS = {
  available: "ok",
  cooling: "rate_limited",
  no_key: "off",
  disabled: "off",
};

const statusLabel = computed(() => STATUS_LABELS[props.provider.status] ?? props.provider.status);
const statusDot = computed(() => STATUS_DOTS[props.provider.status] ?? "off");

function formatRemaining(iso, now) {
  if (!iso) return null;
  const deadline = new Date(iso.includes("T") ? iso : `${iso.replace(" ", "T")}Z`);
  const ms = deadline.getTime() - now;
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "<1 min";
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h`;
}

const cooldownLeft = computed(() =>
  props.provider.status === "cooling"
    ? formatRemaining(props.provider.cooldown_until, props.now)
    : null,
);

const reliability = computed(() => {
  const days = props.provider.recent_window_days;
  const calls = props.provider.recent_calls ?? 0;
  const failures = props.provider.recent_failures ?? 0;
  if (calls === 0) return { text: `no calls · ${days} d`, danger: false };
  if (failures === 0) return { text: `no failures · ${days} d`, danger: false };
  const noun = failures === 1 ? "failure" : "failures";
  return { text: `${failures} ${noun} / ${calls} · ${days} d`, danger: true };
});

const qualityPercent = computed(() => {
  const q = props.provider.quality_bound;
  if (q == null) return null;
  return Math.round(q * 100);
});
</script>

<template>
  <div
    class="provider-card"
    :class="{ 'is-disabled': provider.disabled }"
    data-testid="provider-card"
  >
    <div class="card-top">
      <StatusDot :kind="statusDot" />
      <span class="provider-label">{{ provider.name }}</span>
      <span class="status-badge" :data-status="provider.status">{{ statusLabel }}</span>
      <span v-if="cooldownLeft" class="cooldown-left">{{ cooldownLeft }}</span>
    </div>

    <div class="card-model">{{ provider.model }}</div>

    <div v-if="provider.status === 'no_key' && provider.help" class="key-hint">
      {{ provider.help }}
    </div>

    <div class="meta-row">
      <span class="reliability" :class="{ 'is-danger': reliability.danger }">
        {{ reliability.text }}
      </span>
    </div>

    <div class="quality-row">
      <span v-if="provider.demoted" class="demoted-pill">demoted</span>
      <span v-if="qualityPercent !== null" class="quality-chip">quality {{ qualityPercent }}%</span>
      <span v-else class="quality-chip muted">no ratings yet</span>
    </div>

    <div class="card-actions">
      <button
        type="button"
        class="toggle-btn"
        :class="{ 'is-off': provider.disabled }"
        :aria-label="provider.disabled ? 'Enable provider' : 'Disable provider'"
        @click="emit('toggle')"
      >
        <Power :size="15" />
        <span>{{ provider.disabled ? "Enable" : "Disable" }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.provider-card {
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.625rem 0.75rem;
  margin-bottom: 0.5rem;
  transition: opacity 0.2s;
}

.is-disabled {
  opacity: 0.55;
}

.card-top {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 2px;
}

.provider-label {
  font-weight: 600;
  font-size: 0.9rem;
  color: var(--text);
  flex: 1;
}

.status-badge {
  font-size: 0.68rem;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 999px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  background: var(--border);
  color: var(--muted);
}

.status-badge[data-status="available"] {
  background: rgba(34, 197, 94, 0.15);
  color: var(--success);
}

.status-badge[data-status="cooling"] {
  background: rgba(245, 158, 11, 0.15);
  color: var(--warning);
}

.cooldown-left {
  font-family: var(--font-num);
  font-size: 0.68rem;
  color: var(--warning);
}

.card-model {
  font-family: var(--font-num);
  font-size: 0.78rem;
  color: var(--muted);
  margin-bottom: 0.4rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.key-hint {
  font-size: 0.72rem;
  color: var(--muted);
  background: var(--bg);
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 0.4rem 0.5rem;
  margin-bottom: 0.4rem;
}

.meta-row,
.quality-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.7rem;
  color: var(--muted);
  margin-bottom: 0.3rem;
}

.reliability {
  font-family: var(--font-num);
}

.reliability.is-danger {
  color: var(--danger, #ef4444);
}

.demoted-pill {
  font-size: 0.66rem;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 999px;
  background: rgba(239, 68, 68, 0.15);
  color: var(--danger, #ef4444);
  text-transform: uppercase;
}

.quality-chip {
  font-family: var(--font-num);
}

.quality-chip.muted {
  color: var(--muted-2);
}

.card-actions {
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid var(--border);
  padding-top: 0.35rem;
  margin-top: 0.25rem;
}

.toggle-btn {
  background: none;
  border: 1px solid var(--border);
  color: var(--muted);
  cursor: pointer;
  padding: 0.3rem 0.6rem;
  width: auto;
  display: flex;
  align-items: center;
  gap: 0.3rem;
  border-radius: 8px;
  font-size: 0.72rem;
  transition: color 0.12s, background 0.12s, border-color 0.12s;
}

.toggle-btn:hover {
  color: var(--text);
  border-color: var(--accent);
}

.toggle-btn.is-off {
  color: var(--accent);
  border-color: var(--accent);
}
</style>
