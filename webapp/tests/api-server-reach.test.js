import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  noteAnswer,
  noteFailure,
  readServerReach,
  ServerUnreachable,
} from "../src/api/serverReach.js";

const KEY = "dinary-server-reach";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("HTTP client");
  await allure.story("Server not answering");
  localStorage.clear();
});

afterEach(() => {
  localStorage.clear();
});

describe("serverReach", () => {
  it("keeps when the server last answered across a failure", () => {
    noteAnswer();
    const error = noteFailure("no-answer");

    expect(error).toBeInstanceOf(ServerUnreachable);
    expect(error.kind).toBe("no-answer");
    expect(readServerReach().answeredAt).toBeGreaterThan(0);
    expect(readServerReach().failure).toMatchObject({ kind: "no-answer" });
  });

  it("clears the failure on the next answer", () => {
    noteFailure("app-down");
    noteAnswer();

    expect(readServerReach().failure).toBeNull();
  });

  it.each([
    ["{not json", { answeredAt: null, failure: null }],
    [JSON.stringify({ answeredAt: "yesterday", failure: { kind: "moon", at: 1 } }), { answeredAt: null, failure: null }],
    [JSON.stringify({ answeredAt: 5, failure: { kind: "no-answer", at: 6, extra: 1 } }), { answeredAt: 5, failure: { kind: "no-answer", at: 6 } }],
  ])("reads what storage holds without trusting it (%s)", (stored, expected) => {
    localStorage.setItem(KEY, stored);

    expect(readServerReach()).toEqual(expected);
  });
});
