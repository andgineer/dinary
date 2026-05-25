import { beforeEach, describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import HealthSummaryCard from "../src/components/HealthSummaryCard.vue";

beforeEach(async () => {
  await allure.epic("Components");
  await allure.feature("HealthSummaryCard");
});

function mountCard(health) {
  return mount(HealthSummaryCard, { props: { health } });
}

describe("HealthSummaryCard — counts", () => {
  it("shows healthy / total counts", () => {
    const w = mountCard({ healthy: 3, total: 4, strategy: null, last_switch: null });
    expect(w.text()).toContain("3 / 4 healthy");
  });

  it("shows 0 / 0 when health is null", () => {
    const w = mountCard(null);
    expect(w.text()).toContain("0 / 0 healthy");
  });
});

describe("HealthSummaryCard — status dot", () => {
  it("renders dot-ok when healthy > 0", () => {
    const w = mountCard({ healthy: 2, total: 3, strategy: null, last_switch: null });
    expect(w.find(".dot-ok").exists()).toBe(true);
  });

  it("renders dot-error when healthy is 0", () => {
    const w = mountCard({ healthy: 0, total: 2, strategy: null, last_switch: null });
    expect(w.find(".dot-error").exists()).toBe(true);
  });

  it("renders dot-off when health is null", () => {
    const w = mountCard(null);
    expect(w.find(".dot-off").exists()).toBe(true);
  });
});

describe("HealthSummaryCard — strategy subtitle", () => {
  it("shows strategy when present", () => {
    const w = mountCard({
      healthy: 1,
      total: 1,
      strategy: "round-robin failover",
      last_switch: null,
    });
    expect(w.find(".health-sub").exists()).toBe(true);
    expect(w.text()).toContain("round-robin failover");
  });

  it("omits the subtitle row when strategy is absent", () => {
    const w = mountCard({ healthy: 1, total: 1, strategy: null, last_switch: null });
    expect(w.find(".health-sub").exists()).toBe(false);
  });
});

describe("HealthSummaryCard — add button", () => {
  it("emits add when the + button is clicked", async () => {
    const w = mountCard({ healthy: 1, total: 1, strategy: null, last_switch: null });
    await w.find('[data-testid="add-provider-btn"]').trigger("click");
    expect(w.emitted("add")).toBeTruthy();
  });
});
