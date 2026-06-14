<script setup>
import { ref, watch } from "vue";
import BaseSheet from "./BaseSheet.vue";
import TemplatePreviewPicker from "./TemplatePreviewPicker.vue";
import { useCatalogStore } from "../stores/catalog.js";
import { useToastStore } from "../stores/toast.js";

const catalog = useCatalogStore();
const toast = useToastStore();
const applying = ref(false);

watch(
  () => catalog.templateSwitchOpen,
  (open) => {
    if (!open) return;
    catalog.ensureTemplateCatalog().catch((e) => {
      toast.show(e?.message || "Failed to load category sets", "error");
    });
  },
);

async function apply(code) {
  if (applying.value) return;
  applying.value = true;
  try {
    await catalog.applyTemplate(code, catalog.templateLang);
    catalog.persistTemplateLang();
    toast.show("Category set switched", "success");
    catalog.closeTemplateSwitch();
  } catch (e) {
    toast.show(e?.message || "Failed to switch category set", "error");
  } finally {
    applying.value = false;
  }
}
</script>

<template>
  <BaseSheet
    :open="catalog.templateSwitchOpen"
    :z-index="55"
    aria-label="Switch category set"
    data-testid="template-switch-sheet"
    @close="catalog.closeTemplateSwitch()"
  >
    <template #header>
      <div class="sheet-eyebrow">Switch category set</div>
    </template>

    <p class="switch-hint">
      Switching re-themes groups for the template's categories. Your used categories
      stay; hidden ones stay hidden.
    </p>

    <TemplatePreviewPicker
      :templates="catalog.templateCatalog"
      :lang="catalog.templateLang"
      :active-code="catalog.activeTemplate"
      :applying="applying"
      @apply="apply"
    />
  </BaseSheet>
</template>

<style scoped>
.sheet-eyebrow {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--muted);
  text-transform: uppercase;
}

.switch-hint {
  font-size: 0.82rem;
  color: var(--muted);
  margin: 0 0 0.75rem;
}
</style>
