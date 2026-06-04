import { beforeEach, describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import HeaderSegmented from "../src/components/HeaderSegmented.vue";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("Frontend");
  await allure.story("HeaderSegmented");
});

function mountSeg(tab = "add", showBadge = false) {
  return mount(HeaderSegmented, { props: { tab, showBadge } });
}

const ALL_TABS = ["add", "review", "analytics", "income", "llm"];

describe("HeaderSegmented — all five tabs render", () => {
  it("renders all five tab buttons", () => {
    const w = mountSeg();
    for (const id of ALL_TABS) {
      expect(w.find(`[data-testid="seg-${id}"]`).exists()).toBe(true);
    }
  });

  it("no overflow button or dropdown", () => {
    const w = mountSeg();
    expect(w.find('[data-testid="seg-more"]').exists()).toBe(false);
    expect(w.find('[data-testid="overflow-menu"]').exists()).toBe(false);
  });
});

describe("HeaderSegmented — active state", () => {
  for (const id of ALL_TABS) {
    it(`marks ${id} active when tab=${id}`, () => {
      const w = mountSeg(id);
      expect(w.find(`[data-testid="seg-${id}"]`).classes()).toContain("active");
      for (const other of ALL_TABS.filter((t) => t !== id)) {
        expect(w.find(`[data-testid="seg-${other}"]`).classes()).not.toContain("active");
      }
    });
  }
});

describe("HeaderSegmented — emit", () => {
  for (const id of ALL_TABS) {
    it(`emits update:tab='${id}' on click`, async () => {
      const w = mountSeg("add");
      await w.find(`[data-testid="seg-${id}"]`).trigger("click");
      expect(w.emitted("update:tab")?.[0]?.[0]).toBe(id);
    });
  }
});

describe("HeaderSegmented — badge", () => {
  it("hides badge when showBadge is false", () => {
    const w = mountSeg("add", false);
    expect(w.find(".seg-badge").exists()).toBe(false);
  });

  it("shows badge with ! on review tab when showBadge is true", () => {
    const w = mountSeg("add", true);
    expect(w.find(".seg-badge").text()).toBe("!");
  });

  it("badge has aria-label 'review attention' when shown", () => {
    const w = mountSeg("add", true);
    expect(w.find(".seg-badge").attributes("aria-label")).toBe("review attention");
  });

  it("badge is on the review button", () => {
    const w = mountSeg("add", true);
    expect(w.find('[data-testid="seg-review"] .seg-badge').exists()).toBe(true);
  });
});
