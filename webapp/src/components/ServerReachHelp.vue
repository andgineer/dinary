<script setup>
import { computed } from "vue";
import { readServerReach, REACH_MESSAGES } from "../api/serverReach.js";

const props = defineProps({
  kind: { type: String, required: true },
});

// What failed decides which check comes first; none of them is ruled out by it.
const TAILNET_CHECKS = {
  offline: ["internet"],
  unreachable: ["tailscale", "internet", "vm"],
  "no-answer": ["vm", "internet"],
  "app-down": ["app"],
};

const host = globalThis.location?.hostname || "";
const onTailnet = host.endsWith(".ts.net");
// Tailscale names the machine after the first label and suffixes a taken name, so the
// address is the only reliable source of what to look for in its list.
const machine = host.split(".")[0];

const CHECK_TEXT = {
  internet: "This device is online: other apps and sites open.",
  tailscale: "Tailscale is connected on this device: open its app and connect it if it is off.",
  vm:
    `“${machine}” shows as online in Tailscale's list of machines. If it shows as offline, ` +
    "the server is stopped: in the Oracle Cloud console, open Compute → Instances → the VM " +
    "and press Start. Its address and data are kept, and queued expenses go out by themselves.",
  app:
    "Paste the install prompt into your AI agent again and say that the app stopped " +
    "answering — or, in the dinary checkout on your computer, run " +
    "uv run inv status --prod and uv run inv logs --prod.",
  local: `The dinary server at ${host} is running.`,
};

const checks = computed(() => {
  if (onTailnet) return TAILNET_CHECKS[props.kind];
  return props.kind === "offline" ? ["internet"] : ["local"];
});

const answered = computed(() => {
  const at = readServerReach().answeredAt;
  if (!at) return "It has not answered on this device yet.";
  const time = new Intl.DateTimeFormat(undefined, {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(at));
  return `Last answered on this device: ${time}`;
});
</script>

<template>
  <section class="server-reach" data-testid="server-reach">
    <h3>{{ REACH_MESSAGES[kind] }}</h3>
    <p class="answered">{{ answered }}</p>
    <p>Check, in this order:</p>
    <ol>
      <li v-for="check in checks" :key="check" :data-testid="`check-${check}`">
        {{ CHECK_TEXT[check] }}
      </li>
    </ol>
  </section>
</template>

<style scoped>
.server-reach {
  background: var(--bg);
  border-radius: 8px;
  padding: 0.75rem;
  margin-bottom: 0.75rem;
  font-size: 0.9rem;
  line-height: 1.4;
}

h3 {
  font-size: 1rem;
  margin: 0 0 0.25rem;
}

.answered {
  color: var(--text-muted);
  font-size: 0.8rem;
  margin: 0 0 0.5rem;
}

p {
  margin: 0;
}

ol {
  margin: 0.25rem 0 0;
  padding-left: 1.25rem;
}

li + li {
  margin-top: 0.4rem;
}
</style>
