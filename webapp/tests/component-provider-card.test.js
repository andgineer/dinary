import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ProviderCard from "../src/components/ProviderCard.vue";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("LLM providers");
  await allure.story("ProviderCard");
});

const BASE_PROVIDER = {
  name: "groq-llama",
  model: "llama-3.3-70b-versatile",
  base_url: "https://api.groq.com/openai/v1",
  disabled: false,
  has_key: true,
  cooldown_until: null,
  status: "available",
  call_count: 412,
  last_status: "ok",
  last_at: "2026-05-10T11:30:00+00:00",
  demoted: false,
  quality_bound: null,
  help: null,
};

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("ProviderCard", () => {
  it("renders provider name and model", () => {
    const wrapper = mount(ProviderCard, { props: { provider: BASE_PROVIDER } });
    expect(wrapper.text()).toContain("groq-llama");
    expect(wrapper.text()).toContain("llama-3.3-70b-versatile");
  });

  it("shows call count", () => {
    const wrapper = mount(ProviderCard, { props: { provider: BASE_PROVIDER } });
    expect(wrapper.text()).toContain("412 calls");
  });

  it("shows the available status badge", () => {
    const wrapper = mount(ProviderCard, { props: { provider: BASE_PROVIDER } });
    const badge = wrapper.find(".status-badge");
    expect(badge.attributes("data-status")).toBe("available");
    expect(badge.text()).toBe("available");
  });

  it("shows 'no ratings yet' when quality_bound is null", () => {
    const wrapper = mount(ProviderCard, { props: { provider: BASE_PROVIDER } });
    expect(wrapper.text()).toContain("no ratings yet");
  });

  it("shows numeric quality when quality_bound is present", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: { ...BASE_PROVIDER, quality_bound: 0.83 } },
    });
    expect(wrapper.text()).toContain("quality 83%");
  });

  it("shows demoted pill when demoted", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: { ...BASE_PROVIDER, demoted: true } },
    });
    expect(wrapper.find(".demoted-pill").exists()).toBe(true);
  });

  it("applies is-disabled class and 'Enable' label when disabled", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: { ...BASE_PROVIDER, disabled: true, status: "disabled" } },
    });
    expect(wrapper.find(".provider-card").classes()).toContain("is-disabled");
    expect(wrapper.find(".toggle-btn").text()).toContain("Enable");
  });

  it("shows 'Disable' label when enabled", () => {
    const wrapper = mount(ProviderCard, { props: { provider: BASE_PROVIDER } });
    expect(wrapper.find(".toggle-btn").text()).toContain("Disable");
  });

  it("shows the key onboarding hint for no_key providers", () => {
    const wrapper = mount(ProviderCard, {
      props: {
        provider: {
          ...BASE_PROVIDER,
          has_key: false,
          status: "no_key",
          help: "Create a free key at openrouter.ai/keys.",
        },
      },
    });
    expect(wrapper.find(".key-hint").exists()).toBe(true);
    expect(wrapper.text()).toContain("openrouter.ai/keys");
  });

  it("does not show the key hint when a key is present", () => {
    const wrapper = mount(ProviderCard, { props: { provider: BASE_PROVIDER } });
    expect(wrapper.find(".key-hint").exists()).toBe(false);
  });

  it("shows cooling badge for a cooling provider", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: { ...BASE_PROVIDER, status: "cooling" } },
    });
    expect(wrapper.find(".status-badge").text()).toBe("cooling down");
  });

  it("emits toggle when the power button is clicked", async () => {
    const wrapper = mount(ProviderCard, { props: { provider: BASE_PROVIDER } });
    await wrapper.find(".toggle-btn").trigger("click");
    expect(wrapper.emitted("toggle")).toBeTruthy();
  });
});
