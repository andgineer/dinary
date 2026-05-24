# inv sql — Design Decisions

## Read-only default

The SQL runner opens the database read-only by default. An operator querying
production cannot accidentally mutate the ledger by a typo or by running a
script that expected a different database.

Write mode is an explicit opt-in flag, intended for one-off surgical fixups
under deliberate operator review.

## No write over remote

The write flag is intentionally absent from the remote execution path. The
remote path operates on a snapshot of the live database; writing to a snapshot
that is torn down on exit would silently discard changes. Allowing `--write
--remote` would create a footgun where the operator believes they modified
production but actually modified a throwaway copy.
