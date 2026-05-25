import { beforeEach, describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import BaseSheet from "../src/components/BaseSheet.vue";

beforeEach(async () => {
  await allure.epic("Components");
  await allure.feature("BaseSheet");
});

const TELEPORT_STUB = { props: ["to"], template: "<div><slot /></div>" };

function mountSheet(props = {}, slots = {}) {
  return mount(BaseSheet, {
    props: { open: true, ariaLabel: "Test sheet", ...props },
    slots,
    global: { stubs: { Teleport: TELEPORT_STUB } },
  });
}

describe("BaseSheet — visibility", () => {
  it("renders the sheet when open is true", () => {
    const wrapper = mountSheet({ open: true });
    expect(wrapper.find(".sheet").exists()).toBe(true);
  });

  it("does not render the sheet when open is false", () => {
    const wrapper = mountSheet({ open: false });
    expect(wrapper.find(".sheet").exists()).toBe(false);
  });
});

describe("BaseSheet — slots", () => {
  it("renders header slot content", () => {
    const wrapper = mountSheet({}, { header: "<span class='test-eyebrow'>TITLE</span>" });
    expect(wrapper.find(".test-eyebrow").text()).toBe("TITLE");
  });

  it("renders default slot content in sheet-body", () => {
    const wrapper = mountSheet({}, { default: "<p class='body-content'>body</p>" });
    expect(wrapper.find(".sheet-body .body-content").exists()).toBe(true);
  });

  it("renders footer slot inside sheet-footer when provided", () => {
    const wrapper = mountSheet({}, { footer: "<button class='ok-btn'>OK</button>" });
    expect(wrapper.find(".sheet-footer .ok-btn").exists()).toBe(true);
  });

  it("omits sheet-footer when no footer slot is provided", () => {
    const wrapper = mountSheet();
    expect(wrapper.find(".sheet-footer").exists()).toBe(false);
  });

  it("renders pre-body slot between header and body", () => {
    const wrapper = mountSheet({}, { "pre-body": "<div class='banner'>banner</div>" });
    const banner = wrapper.find(".banner");
    const body = wrapper.find(".sheet-body");
    expect(banner.exists()).toBe(true);
    expect(body.exists()).toBe(true);
  });
});

describe("BaseSheet — close events", () => {
  it("emits close when the close button is clicked", async () => {
    const wrapper = mountSheet();
    await wrapper.find(".sheet-close").trigger("click");
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("emits close when the scrim is clicked", async () => {
    const wrapper = mountSheet();
    await wrapper.find(".sheet-scrim").trigger("click");
    expect(wrapper.emitted("close")).toBeTruthy();
  });
});

describe("BaseSheet — dimmed modifier", () => {
  it("adds sheet-dimmed class when dimmed prop is true", () => {
    const wrapper = mountSheet({ dimmed: true });
    expect(wrapper.find(".sheet").classes()).toContain("sheet-dimmed");
  });

  it("does not add sheet-dimmed when dimmed is false", () => {
    const wrapper = mountSheet({ dimmed: false });
    expect(wrapper.find(".sheet").classes()).not.toContain("sheet-dimmed");
  });
});

describe("BaseSheet — fullHeight modifier", () => {
  it("adds sheet-full class when fullHeight is true", () => {
    const wrapper = mountSheet({ fullHeight: true });
    expect(wrapper.find(".sheet").classes()).toContain("sheet-full");
  });
});

describe("BaseSheet — tall modifier", () => {
  it("adds sheet-tall class when tall is true", () => {
    const wrapper = mountSheet({ tall: true });
    expect(wrapper.find(".sheet").classes()).toContain("sheet-tall");
  });
});
