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
  recent_calls: 41,
  recent_failures: 0,
  recent_window_days: 30,
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

describe("ProviderCard — reliability line", () => {
  it("reports no calls in the window, never 'no failures'", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: { ...BASE_PROVIDER, recent_calls: 0, recent_failures: 0 } },
    });
    const line = wrapper.find(".reliability");
    expect(line.text()).toBe("no calls · 30 d");
    expect(line.text()).not.toContain("no failures");
    expect(line.classes()).not.toContain("is-danger");
  });

  it("reports no failures when every call in the window succeeded", () => {
    const wrapper = mount(ProviderCard, { props: { provider: BASE_PROVIDER } });
    const line = wrapper.find(".reliability");
    expect(line.text()).toBe("no failures · 30 d");
    expect(line.classes()).not.toContain("is-danger");
  });

  it("reports the failure share in danger tone", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: { ...BASE_PROVIDER, recent_calls: 41, recent_failures: 2 } },
    });
    const line = wrapper.find(".reliability");
    expect(line.text()).toBe("2 failures / 41 · 30 d");
    expect(line.classes()).toContain("is-danger");
  });

  it("takes the window length from the response, not a literal", () => {
    const wrapper = mount(ProviderCard, {
      props: {
        provider: { ...BASE_PROVIDER, recent_window_days: 7, recent_calls: 5, recent_failures: 1 },
      },
    });
    expect(wrapper.find(".reliability").text()).toBe("1 failure / 5 · 7 d");
  });

  it("no longer renders a call count or a last-call status", () => {
    const wrapper = mount(ProviderCard, {
      props: { provider: { ...BASE_PROVIDER, call_count: 41, last_status: "error" } },
    });
    expect(wrapper.text()).not.toContain("41 calls");
    expect(wrapper.text()).not.toContain("last:");
  });
});

describe("ProviderCard — cooldown remainder", () => {
  const NOW = Date.parse("2026-09-20T10:00:00Z");

  function mountCooling(cooldownUntil, now = NOW) {
    return mount(ProviderCard, {
      props: {
        provider: { ...BASE_PROVIDER, status: "cooling", cooldown_until: cooldownUntil },
        now,
      },
    });
  }

  it("shows the minutes left next to the badge", () => {
    const wrapper = mountCooling("2026-09-20T10:05:00+00:00");
    expect(wrapper.find(".status-badge").text()).toBe("cooling down");
    expect(wrapper.find(".cooldown-left").text()).toBe("5m");
  });

  it("shows whole hours on a long cooldown", () => {
    const wrapper = mountCooling("2026-09-20T12:30:00+00:00");
    expect(wrapper.find(".cooldown-left").text()).toBe("2h");
  });

  it("shows '<1 min' on the last stretch", () => {
    const wrapper = mountCooling("2026-09-20T10:00:30+00:00");
    expect(wrapper.find(".cooldown-left").text()).toBe("<1 min");
  });

  it("renders no extra span once the deadline has passed", () => {
    const wrapper = mountCooling("2026-09-20T09:55:00+00:00");
    expect(wrapper.find(".status-badge").text()).toBe("cooling down");
    expect(wrapper.find(".cooldown-left").exists()).toBe(false);
  });

  it("renders no extra span without a deadline", () => {
    const wrapper = mountCooling(null);
    expect(wrapper.find(".cooldown-left").exists()).toBe(false);
  });

  it("renders no remainder for a provider that is not cooling", () => {
    const wrapper = mount(ProviderCard, {
      props: {
        provider: { ...BASE_PROVIDER, cooldown_until: "2026-09-20T10:05:00+00:00" },
        now: NOW,
      },
    });
    expect(wrapper.find(".cooldown-left").exists()).toBe(false);
  });

  it("tolerates a space-separated timestamp", () => {
    const wrapper = mountCooling("2026-09-20 10:05:00");
    expect(wrapper.find(".cooldown-left").text()).toBe("5m");
  });
});
