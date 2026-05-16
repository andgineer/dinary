<script setup>
import { computed } from "vue";
import { ChevronUp, ChevronDown, Beaker, Power } from "lucide-vue-next";
import StatusDot from "./StatusDot.vue";

const props = defineProps({
  provider: { type: Object, required: true },
  isFirst: { type: Boolean, default: false },
  isLast: { type: Boolean, default: false },
});
const emit = defineEmits(["edit", "toggle", "move-up", "move-down", "test"]);

const statusKind = computed(() => {
  const p = props.provider;
  if (!p.is_enabled) return "off";
  if (p.rate_limited_until > 0) return "rate_limited";
  if (p.last_status === "error") return "error";
  return "ok";
});

const rateLimitSecsLeft = computed(() => {
  const until = props.provider.rate_limited_until;
  if (!until) return 0;
  return Math.max(0, Math.ceil((until * 1000 - Date.now()) / 1000));
});

const usagePercent = computed(() => {
  const p = props.provider;
  if (!p.limit_today) return null;
  return Math.min(100, (p.used_today / p.limit_today) * 100);
});

const usageBarColor = computed(() => {
  const pct = usagePercent.value;
  if (pct === null) return null;
  return pct > 80 ? "var(--warning)" : "var(--accent)";
});

const latencyColor = computed(() => {
  const ms = props.provider.avg_latency_ms;
  if (ms == null) return "var(--muted)";
  return ms > 3000 ? "var(--warning)" : "var(--muted)";
});
</script>

<template>
  <div
    class="provider-card"
    :class="{ 'is-disabled': !provider.is_enabled }"
    data-testid="provider-card"
  >
    <div
      class="card-body"
      role="button"
      tabindex="0"
      @click="emit('edit')"
      @keydown.enter="emit('edit')"
    >
      <div class="card-top">
        <span class="priority-chip">[{{ provider.priority }}]</span>
        <StatusDot :kind="statusKind" />
        <span class="provider-label">{{ provider.label }}</span>
        <span v-if="rateLimitSecsLeft > 0" class="rate-limit-pill">{{ rateLimitSecsLeft }}s</span>
      </div>
      <div class="card-model">{{ provider.model }}</div>
    </div>

    <div class="usage-row">
      <template v-if="provider.limit_today != null">
        <div class="usage-bar-wrap">
          <div
            class="usage-bar-fill"
            :style="{ width: `${usagePercent}%`, background: usageBarColor }"
          />
        </div>
        <div class="usage-labels">
          <span class="usage-numbers">{{ provider.used_today }} / {{ provider.limit_today }}</span>
          <span class="usage-right">
            today
            <span
              v-if="provider.avg_latency_ms != null"
              class="latency-chip"
              :style="{ color: latencyColor }"
            >{{ provider.avg_latency_ms }}ms</span>
          </span>
        </div>
      </template>
      <template v-else>
        <div class="usage-labels">
          <span class="usage-numbers">{{ provider.used_today ?? 0 }} calls today</span>
          <span class="usage-right">
            no daily cap
            <span
              v-if="provider.avg_latency_ms != null"
              class="latency-chip"
              :style="{ color: latencyColor }"
            >{{ provider.avg_latency_ms }}ms</span>
          </span>
        </div>
      </template>
    </div>

    <div class="card-actions">
      <button
        type="button"
        class="icon-action"
        :disabled="isFirst"
        aria-label="Move up"
        @click.stop="emit('move-up')"
      >
        <ChevronUp :size="15" />
      </button>
      <button
        type="button"
        class="icon-action"
        :disabled="isLast"
        aria-label="Move down"
        @click.stop="emit('move-down')"
      >
        <ChevronDown :size="15" />
      </button>
      <button
        type="button"
        class="icon-action"
        aria-label="Test provider"
        @click.stop="emit('test')"
      >
        <Beaker :size="15" />
      </button>
      <button
        type="button"
        class="icon-action"
        aria-label="Toggle provider"
        @click.stop="emit('toggle')"
      >
        <Power :size="15" :class="{ 'is-off': !provider.is_enabled }" />
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

.card-body {
  cursor: pointer;
}

.card-body:focus {
  outline: none;
}

.card-body:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 4px;
}

.card-top {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 2px;
}

.priority-chip {
  font-family: var(--font-num);
  font-size: 0.72rem;
  color: var(--muted-2);
}

.provider-label {
  font-weight: 600;
  font-size: 0.9rem;
  color: var(--text);
  flex: 1;
}

.rate-limit-pill {
  font-size: 0.68rem;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 999px;
  background: rgba(245, 158, 11, 0.15);
  color: var(--warning);
  border: 1px solid rgba(245, 158, 11, 0.3);
  font-family: var(--font-num);
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

.usage-row {
  margin-bottom: 0.35rem;
}

.usage-bar-wrap {
  height: 3px;
  background: var(--border);
  border-radius: 2px;
  margin-bottom: 3px;
  overflow: hidden;
}

.usage-bar-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.3s;
}

.usage-labels {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.usage-numbers {
  font-family: var(--font-num);
  font-size: 0.7rem;
  color: var(--muted);
}

.usage-right {
  font-size: 0.7rem;
  color: var(--muted);
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.latency-chip {
  font-family: var(--font-num);
  font-size: 0.7rem;
}

.card-actions {
  display: flex;
  gap: 0.25rem;
  justify-content: flex-end;
  border-top: 1px solid var(--border);
  padding-top: 0.35rem;
  margin-top: 0.25rem;
}

.icon-action {
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  padding: 0.25rem;
  width: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  transition: color 0.12s, background 0.12s;
}

.icon-action:hover:not(:disabled) {
  color: var(--text);
  background: var(--border);
}

.icon-action:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.is-off {
  color: var(--muted-2);
}
</style>
