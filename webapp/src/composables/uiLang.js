// Shared UI-language resolution for category templates (onboarding and
// switch-набор): the first two letters of navigator.language, lowercased,
// falling back to "ru" if that locale isn't in the given language set.
export function resolveUiLang(availableLangs) {
  const langs = new Set(availableLangs ?? []);
  const lang = String(navigator.language ?? "").slice(0, 2).toLowerCase();
  return langs.has(lang) ? lang : "ru";
}
