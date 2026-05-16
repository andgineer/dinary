import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ProviderSheet from "../src/components/ProviderSheet.vue";
import { useLlmStore } from "../src/stores/llm.js";

const PROVIDER = {
  id: 5,
  label: "Groq",
  base_url: "https://api.groq.com/openai/v1",
  model: "llama-3.3-70b-versatile",
  is_enabled: true,
  priority: 1,
  last_status: "ok",
  rate_limited_until: 0,
  limit_today: null,
  used_today: 10,
  avg_latency_ms: 500,
};

const TELEPORT_STUB = { props: ["to", "disabled"], template: "<div><slot /></div>" };

function mountSheet(pinia, props = {}) {
  return mount(ProviderSheet, {
    props: { open: true, provider: null, ...props },
    global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB } },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  globalThis.fetch = vi.fn(async () => ({
    ok: true,
    json: async () => ({ providers: [], total: 0, healthy: 0 }),
  }));
});

describe("ProviderSheet — add mode", () => {
  it("renders the sheet in add mode", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mountSheet(pinia);
    await flushPromises();
    expect(wrapper.find('[data-testid="provider-sheet"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("ADD PROVIDER");
    wrapper.unmount();
  });

  it("shows preset chips", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mountSheet(pinia);
    await flushPromises();
    const chips = wrapper.findAll(".preset-chip");
    const names = chips.map((c) => c.text());
    expect(names).toContain("Groq");
    expect(names).toContain("OpenRouter");
    wrapper.unmount();
  });

  it("auto-fills base_url when a preset is selected", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mountSheet(pinia);
    await flushPromises();
    const groqChip = wrapper.findAll(".preset-chip").find((c) => c.text() === "Groq");
    await groqChip.trigger("click");
    const urlInput = wrapper.find("#ps-base-url");
    expect(urlInput.element.value).toContain("groq.com");
    wrapper.unmount();
  });

  it("Submit is disabled when required fields are missing", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mountSheet(pinia);
    await flushPromises();
    expect(wrapper.find(".submit-btn").attributes("disabled")).toBeDefined();
    wrapper.unmount();
  });

  it("emits close when Cancel is clicked", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mountSheet(pinia);
    await flushPromises();
    await wrapper.find(".btn-ghost").trigger("click");
    expect(wrapper.emitted("close")).toBeTruthy();
    wrapper.unmount();
  });
});

describe("ProviderSheet — edit mode", () => {
  it("renders in edit mode with provider data pre-filled", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mountSheet(pinia, { provider: PROVIDER });
    await flushPromises();
    expect(wrapper.text()).toContain("EDIT PROVIDER");
    expect(wrapper.find("#ps-label").element.value).toBe("Groq");
    wrapper.unmount();
  });

  it("shows Remove provider button in edit mode", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mountSheet(pinia, { provider: PROVIDER });
    await flushPromises();
    expect(wrapper.find(".btn-delete").exists()).toBe(true);
    wrapper.unmount();
  });

  it("shows delete confirmation on first Remove click", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mountSheet(pinia, { provider: PROVIDER });
    await flushPromises();
    await wrapper.find(".btn-delete").trigger("click");
    expect(wrapper.find(".delete-confirm").exists()).toBe(true);
    expect(wrapper.text()).toContain("Existing call logs are kept");
    wrapper.unmount();
  });

  it("calls llmStore.remove on second Remove click", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const llm = useLlmStore(pinia);
    const removeSpy = vi.spyOn(llm, "remove").mockResolvedValue();
    const wrapper = mountSheet(pinia, { provider: PROVIDER });
    await flushPromises();
    await wrapper.find(".btn-delete").trigger("click");
    await wrapper.find(".btn-confirm-delete").trigger("click");
    await flushPromises();
    expect(removeSpy).toHaveBeenCalledWith(PROVIDER.id);
    wrapper.unmount();
  });
});
