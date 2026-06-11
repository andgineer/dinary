<script setup>
import { computed, onMounted, ref } from "vue";
import { useCatalogStore } from "../stores/catalog.js";
import { useToastStore } from "../stores/toast.js";
import * as catalogApi from "../api/catalog.js";
import TemplateList from "../components/TemplateList.vue";
import { resolveUiLang } from "../composables/uiLang.js";

const LAST_LANG_KEY = "dinary:catalog:lastLang";

const catalog = useCatalogStore();
const toast = useToastStore();

const templates = ref([]);
const lang = ref("ru");
const applying = ref(false);

const availableLangs = computed(() => Object.keys(templates.value[0]?.names ?? { ru: "" }));

async function init() {
  try {
    templates.value = await catalogApi.listTemplates();
  } catch (e) {
    toast.show(e?.message || "Failed to load category sets", "error");
    return;
  }
  lang.value = resolveUiLang(availableLangs.value);
}

async function apply(code) {
  if (applying.value) return;
  applying.value = true;
  try {
    await catalog.applyTemplate(code, lang.value);
    localStorage.setItem(LAST_LANG_KEY, lang.value);
  } catch (e) {
    toast.show(e?.message || "Failed to apply category set", "error");
  } finally {
    applying.value = false;
  }
}

onMounted(init);
</script>

<template>
  <main class="onboarding" data-testid="onboarding-template">
    <h1>Welcome to Dinary</h1>
    <p class="subtitle">Pick the category set that fits you — you can switch later.</p>

    <div class="lang-row" role="group" aria-label="Language" data-testid="lang-select">
      <button
        v-for="l in availableLangs"
        :key="l"
        type="button"
        class="lang-btn"
        :class="{ 'is-active': l === lang }"
        @click="lang = l"
      >
        {{ l.toUpperCase() }}
      </button>
    </div>

    <TemplateList :templates="templates" :lang="lang" @apply="apply" />
  </main>
</template>

<style scoped>
.onboarding {
  max-width: 480px;
  width: 100%;
  margin: 0 auto;
  padding: 2rem 1.25rem;
}

.onboarding h1 {
  font-size: 1.4rem;
  font-weight: 600;
  margin-bottom: 0.4rem;
}

.subtitle {
  font-size: 0.88rem;
  color: var(--muted);
  margin-bottom: 1.25rem;
}

.lang-row {
  display: flex;
  gap: 0.4rem;
  margin-bottom: 1.25rem;
}

.lang-btn {
  padding: 0.35rem 0.8rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--muted);
  font-size: 0.8rem;
  font-weight: 600;
  cursor: pointer;
  width: auto;
}

.lang-btn.is-active {
  color: var(--text);
  border-color: var(--accent);
}
</style>
