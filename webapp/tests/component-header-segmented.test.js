import { describe, it, expect, afterEach } from "vitest";
import { nextTick } from "vue";
import { mount } from "@vue/test-utils";
import HeaderSegmented from "../src/components/HeaderSegmented.vue";

function mountSeg(tab = "add", doubtfulCount = 0) {
  return mount(HeaderSegmented, { props: { tab, doubtfulCount } });
}

describe("HeaderSegmented — primary buttons", () => {
  it("marks add active when tab=add", () => {
    const w = mountSeg("add");
    expect(w.find('[data-testid="seg-add"]').classes()).toContain("active");
    expect(w.find('[data-testid="seg-review"]').classes()).not.toContain("active");
    expect(w.find('[data-testid="seg-more"]').classes()).not.toContain("active");
  });

  it("marks review active when tab=review", () => {
    const w = mountSeg("review");
    expect(w.find('[data-testid="seg-review"]').classes()).toContain("active");
    expect(w.find('[data-testid="seg-add"]').classes()).not.toContain("active");
    expect(w.find('[data-testid="seg-more"]').classes()).not.toContain("active");
  });

  it("emits update:tab='add' on add click", async () => {
    const w = mountSeg("review");
    await w.find('[data-testid="seg-add"]').trigger("click");
    expect(w.emitted("update:tab")?.[0]?.[0]).toBe("add");
  });

  it("emits update:tab='review' on review click", async () => {
    const w = mountSeg("add");
    await w.find('[data-testid="seg-review"]').trigger("click");
    expect(w.emitted("update:tab")?.[0]?.[0]).toBe("review");
  });
});

describe("HeaderSegmented — badge", () => {
  it("hides badge when doubtfulCount is 0", () => {
    const w = mountSeg("add", 0);
    expect(w.find(".seg-badge").exists()).toBe(false);
  });

  it("shows badge with count when doubtfulCount > 0", () => {
    const w = mountSeg("add", 5);
    expect(w.find(".seg-badge").text()).toBe("5");
  });
});

describe("HeaderSegmented — overflow menu", () => {
  let w;

  afterEach(() => {
    w?.unmount();
  });

  it("menu is hidden by default", () => {
    w = mountSeg("add");
    expect(w.find('[data-testid="overflow-menu"]').exists()).toBe(false);
  });

  it("••• click opens the menu", async () => {
    w = mountSeg("add");
    await w.find('[data-testid="seg-more"]').trigger("click");
    expect(w.find('[data-testid="overflow-menu"]').exists()).toBe(true);
  });

  it("••• click again closes the menu", async () => {
    w = mountSeg("add");
    await w.find('[data-testid="seg-more"]').trigger("click");
    await w.find('[data-testid="seg-more"]').trigger("click");
    expect(w.find('[data-testid="overflow-menu"]').exists()).toBe(false);
  });

  it("menu shows income and llm rows", async () => {
    w = mountSeg("add");
    await w.find('[data-testid="seg-more"]').trigger("click");
    expect(w.find('[data-testid="menu-income"]').exists()).toBe(true);
    expect(w.find('[data-testid="menu-llm"]').exists()).toBe(true);
  });

  it("clicking income row emits update:tab='income' and closes menu", async () => {
    w = mountSeg("add");
    await w.find('[data-testid="seg-more"]').trigger("click");
    await w.find('[data-testid="menu-income"]').trigger("click");
    expect(w.emitted("update:tab")?.[0]?.[0]).toBe("income");
    expect(w.find('[data-testid="overflow-menu"]').exists()).toBe(false);
  });

  it("clicking llm row emits update:tab='llm' and closes menu", async () => {
    w = mountSeg("add");
    await w.find('[data-testid="seg-more"]').trigger("click");
    await w.find('[data-testid="menu-llm"]').trigger("click");
    expect(w.emitted("update:tab")?.[0]?.[0]).toBe("llm");
    expect(w.find('[data-testid="overflow-menu"]').exists()).toBe(false);
  });

  it("••• has active class when income tab is current", () => {
    w = mountSeg("income");
    expect(w.find('[data-testid="seg-more"]').classes()).toContain("active");
  });

  it("••• has active class when llm tab is current", () => {
    w = mountSeg("llm");
    expect(w.find('[data-testid="seg-more"]').classes()).toContain("active");
  });

  it("active menu item has active class", async () => {
    w = mountSeg("income");
    await w.find('[data-testid="seg-more"]').trigger("click");
    expect(w.find('[data-testid="menu-income"]').classes()).toContain("active");
    expect(w.find('[data-testid="menu-llm"]').classes()).not.toContain("active");
  });

  it("Escape closes the menu", async () => {
    w = mountSeg("add");
    await w.find('[data-testid="seg-more"]').trigger("click");
    expect(w.find('[data-testid="overflow-menu"]').exists()).toBe(true);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await nextTick();
    expect(w.find('[data-testid="overflow-menu"]').exists()).toBe(false);
  });

  it("outside pointerdown closes the menu", async () => {
    w = mount(HeaderSegmented, {
      props: { tab: "add", doubtfulCount: 0 },
      attachTo: document.body,
    });
    await w.find('[data-testid="seg-more"]').trigger("click");
    expect(w.find('[data-testid="overflow-menu"]').exists()).toBe(true);
    document.body.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    await nextTick();
    expect(w.find('[data-testid="overflow-menu"]').exists()).toBe(false);
  });
});
