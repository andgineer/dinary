/**
 * "+ New" inline add-only modals for catalog entities.
 *
 * One tiny modal shell reused for all four kinds (group, category,
 * event, tag). Admin endpoints are currently unauthenticated — the
 * shared-token prompt was removed pending a real auth layer; until
 * then network ACLs are the only gate.
 */

import {
  adminAddCategory,
  adminAddEvent,
  adminAddGroup,
  adminAddTag,
} from "./api.js";
import { replaceSnapshot } from "./catalog.js";

function buildModal(title) {
  const overlay = document.createElement("div");
  overlay.className = "modal";
  overlay.style.cssText =
    "display:flex;position:fixed;inset:0;z-index:60;background:rgba(0,0,0,.6);align-items:center;justify-content:center;padding:1rem";
  const box = document.createElement("div");
  box.className = "modal-content";
  box.style.cssText =
    "background:var(--surface,#16213e);border-radius:12px;padding:1.25rem;width:100%;max-width:420px";
  box.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.75rem">
      <h2 style="font-size:1.05rem;font-weight:600;margin:0">${title}</h2>
      <button class="add-modal-close" style="background:none;border:none;color:#94a3b8;font-size:1.75rem;cursor:pointer;padding:.25rem .5rem;line-height:1">&times;</button>
    </div>
    <div class="add-modal-body"></div>
    <div class="add-modal-error" style="color:#f87171;font-size:.85rem;margin-top:.5rem;display:none"></div>
    <div style="display:flex;gap:.5rem;justify-content:flex-end;margin-top:.75rem">
      <button class="add-modal-cancel btn btn-secondary" type="button">Cancel</button>
      <button class="add-modal-submit btn btn-primary" type="button">Add</button>
    </div>
  `;
  overlay.appendChild(box);
  document.body.appendChild(overlay);

  const close = () => {
    document.body.removeChild(overlay);
  };
  box.querySelector(".add-modal-close").onclick = close;
  box.querySelector(".add-modal-cancel").onclick = close;
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  return {
    overlay,
    body: box.querySelector(".add-modal-body"),
    errEl: box.querySelector(".add-modal-error"),
    submitBtn: box.querySelector(".add-modal-submit"),
    close,
  };
}

function wireEnterToSubmit(modal) {
  // Enter on any <input> inside the modal triggers the Submit button,
  // so a keyboard-only flow (tap-in-field -> type -> Enter) works
  // without having to reach for the on-screen "Add" button. We
  // skip <textarea> because those legitimately want Enter to insert a
  // newline, but the add-modals only use <input> elements.
  modal.body.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    if (e.target instanceof HTMLTextAreaElement) return;
    if (modal.submitBtn.disabled) return;
    e.preventDefault();
    modal.submitBtn.click();
  });
}

function showError(errEl, msg) {
  errEl.textContent = msg;
  errEl.style.display = "";
}

// Mirrors ``_DISALLOWED_TAG_NAME_RE`` in ``catalog_writer.py``: the
// ``map`` worksheet splits tag lists on whitespace/commas, so neither
// may appear inside a single tag name. We reject them client-side so
// the operator sees the error immediately instead of after a 422
// round-trip. The server still validates — this is purely a UX
// shortcut.
const TAG_NAME_DISALLOWED = /[,\s]/;

function validateTagName(name) {
  if (!name) return "Enter a name";
  if (TAG_NAME_DISALLOWED.test(name)) {
    return "Tag cannot contain spaces or commas";
  }
  return null;
}

// Per-kind toast templates, keyed on ``kind`` plus ``AddResult.status``.
// Kept per-kind (rather than one generic template) so we can tweak
// wording for a specific entity without forking the template; the
// call site already knows the kind.
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

async function runSubmit(modal, submitFn, kind) {
  const { errEl, submitBtn } = modal;
  errEl.style.display = "none";
  submitBtn.disabled = true;
  try {
    const snapshot = await submitFn();
    replaceSnapshot(snapshot);
    modal.close();
    // Surface the server's AddResult.status to the operator. "created"
    // is the unsurprising happy path (silent); "reactivated" and "noop"
    // change what the operator actually got (an existing inactive row
    // was flipped back on, or nothing changed at all) so emit a toast
    // via a DOM event the app wires up at init. Decoupling through an
    // event keeps catalog-add.js independent of the app's toast helper
    // and lets tests listen in without monkey-patching.
    if (snapshot && snapshot.status) {
      const message = ADD_RESULT_TOASTS[kind]?.[snapshot.status] ?? null;
      document.dispatchEvent(
        new CustomEvent("dinary:catalog-add-result", {
          detail: { status: snapshot.status, kind, message },
        }),
      );
    }
    return snapshot;
  } catch (e) {
    showError(errEl, e.message || String(e));
    submitBtn.disabled = false;
    return null;
  }
}

export function openAddGroup(onAdded) {
  const modal = buildModal("New group");
  modal.body.innerHTML = `
    <label style="display:block;margin-bottom:.5rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">Name</div>
      <input class="f-name" type="text" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
    </label>
  `;
  const nameEl = modal.body.querySelector(".f-name");
  nameEl.focus();
  modal.submitBtn.onclick = async () => {
    const name = nameEl.value.trim();
    if (!name) return showError(modal.errEl, "Enter a name");
    const snap = await runSubmit(modal, () => adminAddGroup({ name }), "group");
    if (snap) onAdded?.(snap.new_id, snap);
  };
  wireEnterToSubmit(modal);
}

export function openAddCategory(groupId, onAdded) {
  const modal = buildModal("New category");
  modal.body.innerHTML = `
    <label style="display:block;margin-bottom:.5rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">Name</div>
      <input class="f-name" type="text" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
    </label>
    <div style="font-size:.75rem;color:#64748b">Group is locked to the one selected in the form.</div>
  `;
  const nameEl = modal.body.querySelector(".f-name");
  nameEl.focus();
  modal.submitBtn.onclick = async () => {
    const name = nameEl.value.trim();
    if (!name) return showError(modal.errEl, "Enter a name");
    if (!groupId) return showError(modal.errEl, "Select a group first");
    const snap = await runSubmit(
      modal,
      () => adminAddCategory({ name, group_id: Number(groupId) }),
      "category",
    );
    if (snap) onAdded?.(snap.new_id, snap);
  };
  wireEnterToSubmit(modal);
}

export function openAddEvent(onAdded) {
  const today = new Date().toISOString().slice(0, 10);
  const modal = buildModal("New event");
  modal.body.innerHTML = `
    <label style="display:block;margin-bottom:.5rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">Name</div>
      <input class="f-name" type="text" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
    </label>
    <label style="display:block;margin-bottom:.5rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">From</div>
      <input class="f-from" type="date" value="${today}" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
    </label>
    <label style="display:block;margin-bottom:.5rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">To</div>
      <input class="f-to" type="date" value="${today}" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
    </label>
    <label style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">
      <input class="f-auto-attach" type="checkbox" style="width:1rem;height:1rem">
      <span style="font-size:.85rem">Auto-fill when expense date matches</span>
    </label>
    <label style="display:block;margin-bottom:.25rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">
        Auto-tags (comma-separated)
      </div>
      <input class="f-auto-tags" type="text" placeholder="e.g.: отпуск, путешествия" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
      <div style="font-size:.7rem;color:#64748b;margin-top:.25rem">
        Automatically attached to the expense when the event is selected.
      </div>
    </label>
  `;
  const nameEl = modal.body.querySelector(".f-name");
  const fromEl = modal.body.querySelector(".f-from");
  const toEl = modal.body.querySelector(".f-to");
  const autoAttachEl = modal.body.querySelector(".f-auto-attach");
  const autoTagsEl = modal.body.querySelector(".f-auto-tags");
  nameEl.focus();
  modal.submitBtn.onclick = async () => {
    const name = nameEl.value.trim();
    if (!name) return showError(modal.errEl, "Enter a name");
    if (!fromEl.value || !toEl.value)
      return showError(modal.errEl, "Specify both dates");
    if (fromEl.value > toEl.value)
      return showError(modal.errEl, "Start date must be <= end date");
    const autoTagsRaw = autoTagsEl.value;
    const autoTags = autoTagsRaw
      .split(/[,\s]+/)
      .map((t) => t.trim())
      .filter((t) => t.length > 0);
    // Individual tokens can only be invalid if the split above
    // produced something empty (already filtered) or contained the
    // separators themselves (impossible by construction). Still, we
    // sanity-check each entry in case the regex ever changes: it's
    // cheap and keeps the client/server contract honest.
    for (const t of autoTags) {
      const tagErr = validateTagName(t);
      if (tagErr) {
        return showError(modal.errEl, `Auto-tag "${t}": ${tagErr}`);
      }
    }
    const snap = await runSubmit(
      modal,
      () =>
        adminAddEvent({
          name,
          date_from: fromEl.value,
          date_to: toEl.value,
          auto_attach_enabled: autoAttachEl.checked,
          auto_tags: autoTags.length > 0 ? autoTags : null,
        }),
      "event",
    );
    if (snap) onAdded?.(snap.new_id, snap);
  };
  wireEnterToSubmit(modal);
}

export function openAddTag(onAdded) {
  const modal = buildModal("New tag");
  modal.body.innerHTML = `
    <label style="display:block;margin-bottom:.5rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">Name</div>
      <input class="f-name" type="text" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
    </label>
  `;
  const nameEl = modal.body.querySelector(".f-name");
  nameEl.focus();
  modal.submitBtn.onclick = async () => {
    const name = nameEl.value.trim();
    const err = validateTagName(name);
    if (err) return showError(modal.errEl, err);
    const snap = await runSubmit(modal, () => adminAddTag({ name }), "tag");
    if (snap) onAdded?.(snap.new_id, snap);
  };
  wireEnterToSubmit(modal);
}
