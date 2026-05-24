import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ProviderCard from "../src/components/ProviderCard.vue";

const BASE_PROVIDER = {
  id: 1,
  priority: 1,
  label: "Groq",
  model: "llama-3.3-70b-versatile",
  is_enabled: true,
  last_status: "ok",
  rate_limited_until: 0,
  limit_today: 14000,
  used_today: 412,
  avg_latency_ms: 940,
};

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("ProviderCard", () => {
  it("renders provider label and model", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: BASE_PROVIDER },
    });
    expect(wrapper.text()).toContain("Groq");
    expect(wrapper.text()).toContain("llama-3.3-70b-versatile");
  });

  it("shows priority chip", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: BASE_PROVIDER },
    });
    expect(wrapper.text()).toContain("[1]");
  });

  it("shows usage bar when limit_today is set", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: BASE_PROVIDER },
    });
    expect(wrapper.find(".usage-bar-fill").exists()).toBe(true);
    expect(wrapper.text()).toContain("412 / 14000");
  });

  it("shows 'no daily cap' when limit_today is null", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: { ...BASE_PROVIDER, limit_today: null } },
    });
    expect(wrapper.text()).toContain("no daily cap");
    expect(wrapper.find(".usage-bar-fill").exists()).toBe(false);
  });

  it("applies is-disabled class when provider is disabled", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: { ...BASE_PROVIDER, is_enabled: false } },
    });
    expect(wrapper.find(".provider-card").classes()).toContain("is-disabled");
  });

  it("disables Move Up button when isFirst", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: BASE_PROVIDER, isFirst: true },
    });
    const upBtn = wrapper.find('[aria-label="Move up"]');
    expect(upBtn.attributes("disabled")).toBeDefined();
  });

  it("disables Move Down button when isLast", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: BASE_PROVIDER, isLast: true },
    });
    const downBtn = wrapper.find('[aria-label="Move down"]');
    expect(downBtn.attributes("disabled")).toBeDefined();
  });

  it("emits edit when card body is clicked", async () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: BASE_PROVIDER },
    });
    await wrapper.find(".card-body").trigger("click");
    expect(wrapper.emitted("edit")).toBeTruthy();
  });

  it("emits toggle when power button is clicked", async () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: BASE_PROVIDER },
    });
    await wrapper.find('[aria-label="Toggle provider"]').trigger("click");
    expect(wrapper.emitted("toggle")).toBeTruthy();
  });

  it("shows error detail when last_error_detail is set", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: { ...BASE_PROVIDER, last_error_detail: "401 Incorrect API key provided" } },
    });
    expect(wrapper.find(".error-detail").exists()).toBe(true);
    expect(wrapper.text()).toContain("401 Incorrect API key provided");
  });

  it("hides error detail when last_error_detail is null", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: { ...BASE_PROVIDER, last_error_detail: null } },
    });
    expect(wrapper.find(".error-detail").exists()).toBe(false);
  });

  it("shows rate-limit pill when rate_limited_until is in the future", () => {
    const futureTs = Math.floor(Date.now() / 1000) + 120;
    const wrapper = mount(ProviderCard, {
      props: { provider: { ...BASE_PROVIDER, rate_limited_until: futureTs } },
    });
    expect(wrapper.find(".rate-limit-pill").exists()).toBe(true);
  });
});
