# Exchange Rates

## Sources

Two upstreams with overlapping coverage:

| Source | Anchor | Coverage | Frequency |
|--------|--------|----------|-----------|
| **NBS** (`kurs.resenje.org`) | RSD | ~40 currencies | Daily (working days) |
| **NBP** (`api.nbp.pl`) | PLN | 148 currencies (Table A: 32 majors; Table B: 116 less-common) | Table A daily; Table B weekly (Wednesdays) |

NBP covers every currency NBS covers (RSD, BAM, MKD, BYN, RUB) plus the full
ECB roster, so the fallback can serve any pair the primary can.

## Resolution policy

- **Pair containing RSD**: NBS direct → NBP bridge through PLN.
- **Pair without RSD**: NBS bridge through RSD → NBP bridge through PLN.

Rates are stored as `1 source_currency * rate = N target_currency`. Both
legs of a pair are written together by `_save_db_rate`.

## NBP date semantics

Table A: any Polish working day → 200; weekend/public holiday → 404.
Table B: only published on Wednesdays; any other day → 404 (up to 6 days
stale on a fallback is acceptable).

When a date-specific lookup returns 404, the client falls back to the
dateless endpoint (`/rates/{table}/{code}/`) which returns the most recently
published rate.
