# Single-use invitation claims

`/api/join` now conditionally deletes the invitation with its expiry predicate before granting membership, switching the session or writing activity. Only a request that deletes exactly one row proceeds. SQLite serializes writes; another request that read the same invitation receives 404 after the winner commits. The claim and all join changes share the existing connection transaction; a later failure rolls the claim back so the invitation can be retried.

The former read-then-join-then-delete flow allowed two concurrent readers to succeed. A deterministic real HTTP/SQLite test demonstrated two 200 responses for different users and for repeated requests by one user. It also demonstrated acceptance when expiry changed after lookup. These cases failed before the change and pass after it.

Four tests in `tests/security/test_invite_concurrency.py` cover:

- Two users racing the same invitation: one 200, one 404, exactly one new membership and activity entry. The losing user's workspace state is unchanged; subsequent replay by either user changes nothing.
- One user racing twice: one successful join and a single activity record.
- Failure while writing join activity: invitation, memberships, sessions and activity are all rolled back; retry succeeds.
- Expiry after lookup and before claim: 404 without membership, session or activity changes.

The tests use real HTTP requests, SQLite connections and transactions. A test-only wrapper pauses both requests immediately after their real invitation lookup, making the dangerous overlap deterministic without arbitrary sleeps. Another wrapper expires the invitation between lookup and claim. SQL, authorization, commits and rollbacks are not mocked. Tests reuse fixture helpers without duplicating the existing nine API security test cases.

Run `python -m unittest discover -s tests/security -p 'test_*.py' -v`. The existing CI discovery includes the new cases; no workflow change is needed.

This fix covers one invitation token in the current SQLite backend, including threaded requests using separate connections. It is not a load benchmark, distributed-database migration, complete concurrency audit, ISO certification or proof that all access-revocation races are closed.

Reference: [SQLite transactions](https://www.sqlite.org/lang_transaction.html).
