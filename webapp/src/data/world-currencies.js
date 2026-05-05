// Bundled subset of ISO 4217 active currency codes used by
// CurrencyPicker.vue's "Add from world list" search.
//
// SCOPE — what's in the list:
//   • Major reserve / global currencies (USD, EUR, GBP, CHF, JPY, CNY,
//     CAD, AUD, NZD).
//   • Balkan neighbours and regional partners (RSD, BAM, BGN, MKD,
//     RON, HUF, SI/EUR-zone, etc.) — the operator lives in Serbia.
//   • Eastern Europe / CIS (PLN, CZK, UAH, MDL, BYN, RUB, GEL, KZT,
//     UZS).
//   • Nordics (SEK, NOK, DKK, ISK).
//   • Middle East and North Africa (AED, ILS, SAR, QAR, EGP, MAD, TRY).
//   • Frequently visited Asian markets (KRW, INR, IDR, MYR, PHP, SGD,
//     HKD, THB, VND).
//   • Big Western-hemisphere economies (BRL, MXN) and ZAR.
// Anything else can be added on demand — keep the list lean so the
// picker stays scannable. Trimmed to ~50 codes; the full ISO 4217
// list is ~170 and would bloat the bundle without changing UX.
//
// SCHEMA: each entry has
//   code     — ISO 4217 alphabetic code (uppercase). Stored value.
//   name     — Human-readable English name.
//   symbols  — Optional list of common shorthand or symbols a user
//              might type from memory (price-tag glyphs, country-
//              specific abbreviations). Both ASCII and Unicode are
//              welcome. The picker matches case-insensitively against
//              substrings, so "kr" lights up SEK / NOK / DKK / ISK
//              and "km" finds BAM. Keep them short and recognisable;
//              don't pad the list with every variant ever printed.
//
// To add more codes later: keep alphabetical order by ``code`` and
// re-run vitest; CurrencyPicker.test.js does not assume a specific
// length, just that searches resolve to expected codes.

export const WORLD_CURRENCIES = [
  { code: "AED", name: "UAE Dirham", symbols: ["د.إ", "DH"] },
  { code: "AUD", name: "Australian Dollar", symbols: ["A$"] },
  { code: "BAM", name: "Bosnia-Herzegovina Convertible Mark", symbols: ["KM"] },
  { code: "BGN", name: "Bulgarian Lev", symbols: ["лв", "lv"] },
  { code: "BRL", name: "Brazilian Real", symbols: ["R$"] },
  { code: "BYN", name: "Belarusian Ruble", symbols: ["Br", "Br."] },
  { code: "CAD", name: "Canadian Dollar", symbols: ["C$", "CA$"] },
  { code: "CHF", name: "Swiss Franc", symbols: ["Fr", "SFr"] },
  { code: "CNY", name: "Chinese Yuan", symbols: ["¥", "RMB", "元"] },
  { code: "CZK", name: "Czech Koruna", symbols: ["Kč"] },
  { code: "DKK", name: "Danish Krone", symbols: ["kr", "kr."] },
  { code: "EGP", name: "Egyptian Pound", symbols: ["£", "ج.م", "E£"] },
  { code: "EUR", name: "Euro", symbols: ["€"] },
  { code: "GBP", name: "Pound Sterling", symbols: ["£"] },
  { code: "GEL", name: "Georgian Lari", symbols: ["₾", "ლ"] },
  { code: "HKD", name: "Hong Kong Dollar", symbols: ["HK$"] },
  { code: "HUF", name: "Hungarian Forint", symbols: ["Ft"] },
  { code: "IDR", name: "Indonesian Rupiah", symbols: ["Rp"] },
  { code: "ILS", name: "Israeli New Shekel", symbols: ["₪", "NIS"] },
  { code: "INR", name: "Indian Rupee", symbols: ["₹", "Rs"] },
  { code: "ISK", name: "Icelandic Krona", symbols: ["kr"] },
  { code: "JPY", name: "Japanese Yen", symbols: ["¥", "円"] },
  { code: "KRW", name: "South Korean Won", symbols: ["₩"] },
  { code: "KZT", name: "Kazakhstani Tenge", symbols: ["₸"] },
  { code: "MAD", name: "Moroccan Dirham", symbols: ["د.م", "DH"] },
  { code: "MDL", name: "Moldovan Leu", symbols: ["L", "lei"] },
  { code: "MKD", name: "Macedonian Denar", symbols: ["ден", "den"] },
  { code: "MXN", name: "Mexican Peso", symbols: ["$", "Mex$"] },
  { code: "MYR", name: "Malaysian Ringgit", symbols: ["RM"] },
  { code: "NOK", name: "Norwegian Krone", symbols: ["kr"] },
  { code: "NZD", name: "New Zealand Dollar", symbols: ["NZ$"] },
  { code: "PHP", name: "Philippine Peso", symbols: ["₱"] },
  { code: "PLN", name: "Polish Zloty", symbols: ["zł"] },
  { code: "QAR", name: "Qatari Riyal", symbols: ["﷼", "QR"] },
  { code: "RON", name: "Romanian Leu", symbols: ["lei", "L"] },
  { code: "RSD", name: "Serbian Dinar", symbols: ["дин", "din"] },
  { code: "RUB", name: "Russian Ruble", symbols: ["₽"] },
  { code: "SAR", name: "Saudi Riyal", symbols: ["﷼", "SR"] },
  { code: "SEK", name: "Swedish Krona", symbols: ["kr"] },
  { code: "SGD", name: "Singapore Dollar", symbols: ["S$"] },
  { code: "THB", name: "Thai Baht", symbols: ["฿"] },
  { code: "TRY", name: "Turkish Lira", symbols: ["₺", "TL"] },
  { code: "UAH", name: "Ukrainian Hryvnia", symbols: ["₴", "грн"] },
  { code: "USD", name: "United States Dollar", symbols: ["$", "US$"] },
  { code: "UZS", name: "Uzbekistani Som", symbols: ["сўм", "soʻm"] },
  { code: "VND", name: "Vietnamese Dong", symbols: ["₫"] },
  { code: "ZAR", name: "South African Rand", symbols: ["R"] },
];
