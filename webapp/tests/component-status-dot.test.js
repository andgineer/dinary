import { beforeEach, describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import StatusDot from "../src/components/StatusDot.vue";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("Frontend");
  await allure.story("StatusDot");
});

const KINDS = ["ok", "rate_limited", "off", "error"];

describe("StatusDot", () => {
  it.each(KINDS)("renders the correct class for kind=%s", (kind) => {
    const wrapper = mount(StatusDot, { props: { kind } });
    expect(wrapper.find(".status-dot").classes()).toContain(`dot-${kind}`);
  });

  it("defaults to off when no kind is supplied", () => {
    const wrapper = mount(StatusDot, { props: {} });
    expect(wrapper.find(".status-dot").classes()).toContain("dot-off");
  });

  it("is aria-hidden", () => {
    const wrapper = mount(StatusDot, { props: { kind: "ok" } });
    expect(wrapper.find(".status-dot").attributes("aria-hidden")).toBe("true");
  });
});
