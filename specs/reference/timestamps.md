# Timestamp Policy

## Rule

All `TIMESTAMP` columns in the DB are stored with TZ offset info.
Before writing, every datetime is converted to the user's configured timezone
(`settings.user_timezone`, default `"Europe/Belgrade"`).

## Why

Preserving the TZ offset lets a future migration correctly reconstruct the
absolute moment in time if the user moves to a different timezone.
`ORDER BY datetime DESC` on the stored strings works correctly within the same
UTC offset (i.e. within one DST season). A rare cross-DST comparison (e.g. a
+01:00 CET receipt vs. a +02:00 CEST receipt in the same paginated view) may
be off by up to 1 hour — accepted, since it affects at most one day's ordering
around DST transitions.

## Manual expenses (POST /api/expenses)

The PWA sends `expense_datetime` as a full ISO 8601 string with the client's
local TZ offset, e.g. `"2026-05-22T15:30:00+02:00"`.

The server converts it to user TZ before storing:

```python
expense_dt = req.expense_datetime.astimezone(ZoneInfo(settings.user_timezone))
```

The **date portion** (used for exchange-rate lookup and the `month` response
field) comes from the client-supplied datetime after conversion to user TZ —
so the accounting date matches what the user sees on their device.

## Receipt expenses

`sdcDateTime` from the Serbian eFiscal API already carries a full TZ-aware
timestamp (e.g. `"2026-05-22T14:32:00+02:00"`). The pipeline parses it and
converts to user TZ:

```python
receipt_dt_obj = datetime.fromisoformat(receipt_dt_raw).astimezone(ZoneInfo(settings.user_timezone))
```

This `datetime` object is passed directly to the `expenses` INSERT; the
`storage._adapt_datetime` adapter formats it as `"2026-05-22 14:32:00+02:00"`.

## SQLite adapter

`storage._adapt_datetime` calls `value.isoformat(sep=" ")`, which for a
TZ-aware datetime produces `"YYYY-MM-DD HH:MM:SS+HH:MM"`.
`storage._convert_datetime` reads it back via `datetime.fromisoformat`.

All connections use `detect_types=sqlite3.PARSE_DECLTYPES`, so `TIMESTAMP`
columns are auto-converted on read.
