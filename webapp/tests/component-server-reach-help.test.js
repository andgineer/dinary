import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import ServerReachHelp from "../src/components/ServerReachHelp.vue";

beforeEach(async () => {
  await allure.epic("PWA");
  await allure.feature("Frontend");
  await allure.story("Server not answering");
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function help(kind, hostname = "dinary-1.tail1234.ts.net") {
  vi.stubGlobal("location", { hostname });
  return mount(ServerReachHelp, { props: { kind } });
}

function checks(wrapper) {
  return wrapper.findAll("li").map((item) => item.attributes("data-testid"));
}

describe("ServerReachHelp", () => {
  it("puts the stopped server first when the server did not answer at all", () => {
    localStorage.setItem("dinary-server-reach", JSON.stringify({ answeredAt: Date.UTC(2026, 9, 1), failure: null }));

    const wrapper = help("no-answer");

    expect(wrapper.get("h3").text()).toBe("The server isn't answering.");
    expect(wrapper.text()).toContain("Last answered on this device:");
    expect(checks(wrapper)).toEqual(["check-vm", "check-internet"]);
    expect(wrapper.get('[data-testid="check-vm"]').text()).toContain("“dinary-1” shows as online");
    expect(wrapper.get('[data-testid="check-vm"]').text()).toContain(
      "Oracle Cloud console, open Compute → Instances → the VM and press Start",
    );
    expect(wrapper.text()).not.toContain("Pay As You Go");
  });

  it("puts Tailscale on this device first when the server's name did not resolve", () => {
    const wrapper = help("unreachable");

    expect(wrapper.text()).toContain("It has not answered on this device yet.");
    expect(checks(wrapper)).toEqual(["check-tailscale", "check-internet", "check-vm"]);
  });

  it("sends the reader to the install agent when the VM answers for a stopped app", () => {
    const wrapper = help("app-down");

    expect(checks(wrapper)).toEqual(["check-app"]);
    expect(wrapper.text()).toContain("uv run inv status --prod");
  });

  it("leaves Tailscale and Oracle out for a server outside a tailnet", () => {
    const wrapper = help("no-answer", "dinary.example.com");

    expect(checks(wrapper)).toEqual(["check-local"]);
    expect(wrapper.text()).toContain("The dinary server at dinary.example.com is running.");
  });
});
