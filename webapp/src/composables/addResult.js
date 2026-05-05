// Per-kind toast templates for the AddResult.status returned by admin
// add endpoints.

const ADD_RESULT_TOASTS = {
  group: {
    reactivated: "Inactive group restored — check its fields",
    noop: "Such a group already exists — no changes",
  },
  category: {
    reactivated: "Inactive category restored — check its fields",
    noop: "Such a category already exists — no changes",
  },
  event: {
    reactivated: "Inactive event restored — check its fields",
    noop: "Such an event already exists — no changes",
  },
  tag: {
    reactivated: "Inactive tag restored — check its fields",
    noop: "Such a tag already exists — no changes",
  },
};

export function addResultMessage(kind, status) {
  return ADD_RESULT_TOASTS[kind]?.[status] ?? null;
}

// Mirrors _DISALLOWED_TAG_NAME_RE in catalog_writer.py — the ``map``
// worksheet splits tag lists on whitespace/commas, so neither may
// appear inside a single tag name.
export const TAG_NAME_DISALLOWED_RE = /[,\s]/;

export function validateTagName(name) {
  if (!name) return "Enter a name";
  if (TAG_NAME_DISALLOWED_RE.test(name)) {
    return "Tag cannot contain spaces or commas";
  }
  return null;
}
