/**
 * "+ Новый" inline add-only modals for catalog entities.
 *
 * One tiny modal shell reused for all four kinds (group, category,
 * event, tag). The admin API is token-protected — if no admin token
 * is set in localStorage the modal offers a first-run prompt to
 * paste it.
 */

import {
  adminAddCategory,
  adminAddEvent,
  adminAddGroup,
  adminAddTag,
  hasAdminToken,
  setAdminToken,
} from "./api.js";
import { replaceSnapshot } from "./catalog.js";

function ensureAdminToken() {
  if (hasAdminToken()) return true;
  // eslint-disable-next-line no-alert
  const token = prompt("Paste the admin API token (one-time setup):");
  if (!token) return false;
  setAdminToken(token.trim());
  return hasAdminToken();
}

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
      <button class="add-modal-cancel btn btn-secondary" type="button">Отмена</button>
      <button class="add-modal-submit btn btn-primary" type="button">Добавить</button>
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
  // without having to reach for the on-screen "Добавить" button. We
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

// Per-kind toast templates. Russian has three grammatical genders; the
// app.js handler used to splice the bare kind noun into a generic
// template (``Восстановлена неактивная ${kind}``) which produced
// broken agreement for neuter ``событие`` and masculine ``тэг``. We
// render the final message here — the kind is already known at the
// call site and it keeps the genders with the nouns they agree with.
// Templates are keyed on ``kind`` plus ``AddResult.status``.
const ADD_RESULT_TOASTS = {
  группа: {
    reactivated: "Восстановлена неактивная группа — проверьте поля",
    noop: "Такая группа уже существует — без изменений",
  },
  категория: {
    reactivated: "Восстановлена неактивная категория — проверьте поля",
    noop: "Такая категория уже существует — без изменений",
  },
  событие: {
    reactivated: "Восстановлено неактивное событие — проверьте поля",
    noop: "Такое событие уже существует — без изменений",
  },
  тэг: {
    reactivated: "Восстановлен неактивный тэг — проверьте поля",
    noop: "Такой тэг уже существует — без изменений",
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
  if (!ensureAdminToken()) return;
  const modal = buildModal("Новая группа");
  modal.body.innerHTML = `
    <label style="display:block;margin-bottom:.5rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">Название</div>
      <input class="f-name" type="text" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
    </label>
  `;
  const nameEl = modal.body.querySelector(".f-name");
  nameEl.focus();
  modal.submitBtn.onclick = async () => {
    const name = nameEl.value.trim();
    if (!name) return showError(modal.errEl, "Введите название");
    const snap = await runSubmit(modal, () => adminAddGroup({ name }), "группа");
    if (snap) onAdded?.(snap.new_id, snap);
  };
  wireEnterToSubmit(modal);
}

export function openAddCategory(groupId, onAdded) {
  if (!ensureAdminToken()) return;
  const modal = buildModal("Новая категория");
  modal.body.innerHTML = `
    <label style="display:block;margin-bottom:.5rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">Название</div>
      <input class="f-name" type="text" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
    </label>
    <div style="font-size:.75rem;color:#64748b">Группа зафиксирована: выбранная в форме.</div>
  `;
  const nameEl = modal.body.querySelector(".f-name");
  nameEl.focus();
  modal.submitBtn.onclick = async () => {
    const name = nameEl.value.trim();
    if (!name) return showError(modal.errEl, "Введите название");
    if (!groupId) return showError(modal.errEl, "Сначала выберите группу");
    const snap = await runSubmit(
      modal,
      () => adminAddCategory({ name, group_id: Number(groupId) }),
      "категория",
    );
    if (snap) onAdded?.(snap.new_id, snap);
  };
  wireEnterToSubmit(modal);
}

export function openAddEvent(onAdded) {
  if (!ensureAdminToken()) return;
  const today = new Date().toISOString().slice(0, 10);
  const modal = buildModal("Новое событие");
  modal.body.innerHTML = `
    <label style="display:block;margin-bottom:.5rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">Название</div>
      <input class="f-name" type="text" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
    </label>
    <label style="display:block;margin-bottom:.5rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">С</div>
      <input class="f-from" type="date" value="${today}" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
    </label>
    <label style="display:block;margin-bottom:.5rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">По</div>
      <input class="f-to" type="date" value="${today}" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
    </label>
  `;
  const nameEl = modal.body.querySelector(".f-name");
  const fromEl = modal.body.querySelector(".f-from");
  const toEl = modal.body.querySelector(".f-to");
  nameEl.focus();
  modal.submitBtn.onclick = async () => {
    const name = nameEl.value.trim();
    if (!name) return showError(modal.errEl, "Введите название");
    if (!fromEl.value || !toEl.value)
      return showError(modal.errEl, "Укажите обе даты");
    if (fromEl.value > toEl.value)
      return showError(modal.errEl, "Дата начала должна быть <= дате конца");
    const snap = await runSubmit(
      modal,
      () =>
        adminAddEvent({
          name,
          date_from: fromEl.value,
          date_to: toEl.value,
        }),
      "событие",
    );
    if (snap) onAdded?.(snap.new_id, snap);
  };
  wireEnterToSubmit(modal);
}

export function openAddTag(onAdded) {
  if (!ensureAdminToken()) return;
  const modal = buildModal("Новый тэг");
  modal.body.innerHTML = `
    <label style="display:block;margin-bottom:.5rem">
      <div style="font-size:.8rem;color:#94a3b8;margin-bottom:.25rem">Название</div>
      <input class="f-name" type="text" style="width:100%;padding:.5rem;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff">
    </label>
  `;
  const nameEl = modal.body.querySelector(".f-name");
  nameEl.focus();
  modal.submitBtn.onclick = async () => {
    const name = nameEl.value.trim();
    if (!name) return showError(modal.errEl, "Введите название");
    const snap = await runSubmit(modal, () => adminAddTag({ name }), "тэг");
    if (snap) onAdded?.(snap.new_id, snap);
  };
  wireEnterToSubmit(modal);
}
