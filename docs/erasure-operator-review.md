# Dormant operator and committed-DRAFT review

This is an additive **source preparation** boundary, not an activated deletion
pipeline. Existing DRAFT preparation, identity registry, journal protocols,
application endpoints, account recovery and database schema remain unchanged.

## One bounded observation

1. A trusted dedicated Linux process pins the operator's numeric UID and its
   own private Unix-socket directory. It supplies the reviewer implementation
   and database binding out of band. Root, kernel, source/configuration integrity
   and the authorized operator remain trusted.
2. The server checks real `SO_PEERCRED` before reading a request or sending a
   challenge. The client checks the server's real UID before disclosing IDs;
   matching socket-file metadata alone is insufficient. Nonroot, capability-free
   principals and `no_new_privs` reuse the existing custodian boundary.
3. A fresh server UUID challenge binds one connection to one `review` request.
   Only canonical account and DRAFT UUIDs are accepted, with an exact schema,
   1 KiB frame bound and finite session deadline. Caller-supplied UID, approval,
   policy, closure epoch, URL, handler or command is not accepted. No reconnect,
   automatic retry, resume or persistent approval ticket is produced.
4. The configured PostgreSQL reviewer uses a new physical connection (`NullPool`)
   and its own read-only repeatable-read transaction. It pins database name/OID
   and login/current role, checks the restricted role and target-table access,
   and reads only the DRAFT and minimal lifecycle columns. A caller's ORM session,
   uncommitted flush, stale identity map or previous connection snapshot is not
   used. The policy is evaluated at the database observation clock.
5. The result is `COMMITTED_DRAFT_REVIEW_ONLY`. Identity enrollment, journal
   dispatch, purge and restore flags are always false. Return occurs only after
   DB transaction/connection cleanup succeeds. Any protocol, peer, timeout,
   storage or cleanup ambiguity fails closed without a success response or raw
   request, SQL parameters, connection URL or secret in an error.

## Explicit limits and next boundary

The database observation is a snapshot, **not a lock or a live fence** against
subsequent recovery, a changed closure epoch or transaction rollback elsewhere.
Database name/OID is an operator pin, not globally unique anti-clone provenance.
It does not detect restoration of an entire database or a malicious database
administrator. Linux UID checks do not separate compromised code sharing the
same authorized UID. A frozen Python object is not a cross-process capability.

Async deadlines use cooperative cancellation, not OS-level forced termination.
A non-cooperative reviewer/driver still requires the separate dedicated-worker
watchdog gate; this source stage does not install or prove that hard-stop link.

No registry method is called. A successful observation cannot be fed directly
into enrollment or deletion as an authorization permit. Working placement must
still establish reviewed source/profile ownership, actual dedicated principals,
read-only credentials, independent identity storage and latest-state/freshness
checks at the mutation boundary. Joint custody-storage rollback remains an
explicitly unproved risk from the previous registry stage.

The adapter is one-shot reference code, without a service, CLI activation,
public endpoint, key loader, migration or installation. Existing socket paths
are retained and refused, never unlinked or replaced automatically. Operational
activation and real identifiers require their own reviewed deployment gate.

## Evidence

The ordinary tests exercise canonical framing, forged fields, replayed challenge,
timeouts and nonadmitting observations. A separate mandatory Linux proof uses
real unprivileged subprocess UIDs and a real Unix listener (not a socketpair
whose credentials could describe its creator). Its synthetic callback proves
the peer/protocol layer, not PostgreSQL semantics.

A separate mandatory PostgreSQL 16 gate uses a fresh nonce database and a
non-owner reader role with no target-table writes. It proves actual committed
visibility, rollback isolation, fresh observations and unchanged application
rows. No SQLite substitution or skipped/partial proof counts as PostgreSQL PASS.
All fixtures use artificial data, no working keys or cloud resources.
