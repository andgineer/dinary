// What the device last learned about reaching the server, kept in localStorage so a
// reopened app still knows when the server last answered.

const STORAGE_KEY = "dinary-server-reach";

export const REACH_KINDS = ["offline", "unreachable", "no-answer", "app-down"];

export const REACH_MESSAGES = {
  offline: "This device is offline.",
  unreachable: "Can't reach the server.",
  "no-answer": "The server isn't answering.",
  "app-down": "The server is on, but dinary isn't running on it.",
};

export class ServerUnreachable extends Error {
  constructor(kind) {
    super(REACH_MESSAGES[kind]);
    this.name = "ServerUnreachable";
    this.kind = kind;
  }
}

export function readServerReach() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (!saved || typeof saved !== "object") return { answeredAt: null, failure: null };
    const failure = saved.failure;
    return {
      answeredAt: Number.isFinite(saved.answeredAt) ? saved.answeredAt : null,
      failure:
        REACH_KINDS.includes(failure?.kind) && Number.isFinite(failure.at)
          ? { kind: failure.kind, at: failure.at }
          : null,
    };
  } catch {
    return { answeredAt: null, failure: null };
  }
}

function saveServerReach(reach) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(reach));
  } catch {
    // the note only lives as long as this page
  }
}

export function noteAnswer() {
  saveServerReach({ answeredAt: Date.now(), failure: null });
}

export function noteFailure(kind) {
  saveServerReach({ ...readServerReach(), failure: { kind, at: Date.now() } });
  return new ServerUnreachable(kind);
}
