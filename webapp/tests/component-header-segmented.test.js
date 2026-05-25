import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import HeaderSegmented from "../src/components/HeaderSegmented.vue";

function mountSeg(tab = "add", doubtfulCount = 0) {
  return mount(HeaderSegmented, { props: { tab, doubtfulCount } });
}

describe("HeaderSegmented", () => {
  it("marks the add button active when tab=add", () => {
    const w = mountSeg("add");
    expect(w.find('[data-testid="seg-add"]').classes()).toContain("active");
    expect(w.find('[data-testid="seg-income"]').classes()).not.toContain("active");
    expect(w.find('[data-testid="seg-review"]').classes()).not.toContain("active");
    expect(w.find('[data-testid="seg-llm"]').classes()).not.toContain("active");
  });

  it("marks the income button active when tab=income", () => {
    const w = mountSeg("income");
    expect(w.find('[data-testid="seg-income"]').classes()).toContain("active");
    expect(w.find('[data-testid="seg-add"]').classes()).not.toContain("active");
  });

  it("marks the review button active when tab=review", () => {
    const w = mountSeg("review");
    expect(w.find('[data-testid="seg-review"]').classes()).toContain("active");
    expect(w.find('[data-testid="seg-add"]').classes()).not.toContain("active");
  });

  it("marks the llm button active when tab=llm", () => {
    const w = mountSeg("llm");
    expect(w.find('[data-testid="seg-llm"]').classes()).toContain("active");
  });

  it("hides badge when doubtfulCount is 0", () => {
    const w = mountSeg("add", 0);
    expect(w.find(".seg-badge").exists()).toBe(false);
  });

  it("shows badge with count when doubtfulCount > 0", () => {
    const w = mountSeg("add", 5);
    expect(w.find(".seg-badge").exists()).toBe(true);
    expect(w.find(".seg-badge").text()).toBe("5");
  });

  it("emits update:tab with 'review' when the review button is clicked", async () => {
    const w = mountSeg("add");
    await w.find('[data-testid="seg-review"]').trigger("click");
    expect(w.emitted("update:tab")).toBeTruthy();
    expect(w.emitted("update:tab")[0][0]).toBe("review");
  });

  it("emits update:tab with 'llm' when the llm button is clicked", async () => {
    const w = mountSeg("add");
    await w.find('[data-testid="seg-llm"]').trigger("click");
    expect(w.emitted("update:tab")[0][0]).toBe("llm");
  });

  it("emits update:tab with 'add' when the add button is clicked", async () => {
    const w = mountSeg("review");
    await w.find('[data-testid="seg-add"]').trigger("click");
    expect(w.emitted("update:tab")[0][0]).toBe("add");
  });

  it("emits update:tab with 'income' when the income button is clicked", async () => {
    const w = mountSeg("add");
    await w.find('[data-testid="seg-income"]').trigger("click");
    expect(w.emitted("update:tab")[0][0]).toBe("income");
  });
});
