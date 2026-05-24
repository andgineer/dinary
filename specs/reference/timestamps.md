# Timestamp Policy

## Store with timezone offset

All timestamps are stored with a timezone offset, converted to the user's
configured timezone before write. Preserving the offset allows a future migration
to correctly reconstruct the absolute moment in time if the user moves to a
different timezone — a timezone-naive timestamp would make that reconstruction
ambiguous or impossible.

## DST ordering trade-off

Sorting timestamps as strings works correctly within a single UTC offset. Across
a DST boundary (e.g. a +01:00 CET receipt and a +02:00 CEST receipt in the same
paginated view) ordering may be off by up to one hour. This is accepted: DST
transitions affect at most one day's ordering per year, and the cost of
normalising all stored timestamps to UTC for correct sort order outweighs the
impact of the rare edge case.

## Client-supplied timestamps

The PWA sends a full ISO 8601 timestamp with the client's local timezone offset.
The server converts it to the user's configured timezone before storing. This
means the accounting date (used for exchange-rate lookup and display) matches what
the user sees on their device, not what UTC would report.
