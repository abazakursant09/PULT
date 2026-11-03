# Yandex cabinet safety hold — B2 policy contract

Status: implementation candidate; not deployment or marketplace quota certification.

## Authority and compatibility

This is an application-owned conservative safety policy. It does not establish
Yandex's exact quota buckets, concurrency limits, or coverage of other applications.
The B1 physical columns `ym_review_read_not_before` and
`ym_review_read_indeterminate` retain their names and all stored values. B2 expands
their logical readers/writers without DDL, clearing holds, or changing defaults.
The indeterminate bit is sticky; key rotation, success, and a user retry do not
release it. Existing B1 read-only interfaces retain their narrow contract.

The managed B2 profile requires a server-owned verified cabinet and a proven
current credential-to-cabinet binding. Historical verification status and a cached
business ID alone are not proof for a rotated credential. Missing provenance is
an explicit unmanaged/bootstrap exclusion, not proof of protection. Loss of a
previously selected managed identity or database authority fails closed.

## Selected operations and exclusions

The intended managed operation set comprises review/comment reads, review reply
publication, known-bound campaign discovery/list/link, verification probes, and
existing products/cards, prices, stocks, orders, returns and promotions reads.
Each concrete operation must select its actual client and bound cabinet explicitly;
HTTP method, URL substrings and repeat-safe flags do not infer policy membership.
Current implementation/test evidence must enumerate actual coverage before release.

Unknown first-key discovery, unverified/legacy or unproven key bindings, direct
unbound calls, OAuth/ADVERTISER, unsupported actions and external clients are
excluded. B2 does not activate previously inert reconciliation or unsupported
actions to make a coverage claim. Other marketplaces and financial calculations
are unchanged.

## New writes and resumption

Original authorization, ownership, containment, scope, payload, capability,
automation, operation-key and legacy checks retain precedence. Cached outcomes
are resolved before a new hold admission; a cached result is not a new send.
Failed, ambiguous, pending and mismatched keys never become reusable merely
because a hold expired.

A new managed write is admitted with the original pending-claim transaction.
The account authority lock is held through that transaction's confirmed commit.
Denied admission creates no claim or attempt and does not commit/roll back
unrelated caller work. A connection-level savepoint, not an ORM nested transaction
that implicitly flushes pending objects, bounds ordinary denial. Preexisting caller
locks cannot be promised released while preserving that caller's transaction.
Unsupported dirty-session lock ordering must be refused before the account lock.

The admission-to-claim-commit section has a bounded database budget. Timeout or
lost acknowledgement once commit is attempted is uncertain, not a proven
unclaimed deferral. It permits no dispatch or automatic key reopening.

Only a confirmed original claim commit can yield an ephemeral first-send permit.
It belongs to the creating task/invocation, its own inserted generation, operation
identity, immutable request and exact credential snapshot. It cannot be copied to
another task, client, request, retry, resumed operation or process. Plaintext tokens,
ORM rows and database sessions are not stored in context variables.

The existing generation fence and single shared dispatch site remain. No database
query is added between a successful fence commit and the first permitted send.
An operation admitted before a later hold can send its one pre-admitted request
afterward; this is admission ordering, not late cancellation or a recent-send
guarantee. No invented permit TTL strands a committed claim.

Operator resumption keeps cached correlation resolution first, existing-log then
account lock ordering, and the original audit/CAS commit. Denial creates no applied
audit or counted attempt. A resumed operation requires its own new admission and
never inherits an earlier permit.

## Outcomes and observation

Preclaim deferral is an unsuccessful, zero-send result, not success, ambiguous
delivery or a counted provider failure. Manual/automatic publication preserves
attempts, status, text and execution-log linkage on this branch. Finite scheduling
uses the maximum existing/deadline bound; authority/indeterminate failure invents no
provider deadline. The decision-confirm response retains its existing truthful
unsuccessful status contract; generic HTTP mapping is not claimed for every caller.

Actual dispatched throttles and ambiguous transport failures retain existing
attempt/replay accounting. Raw throttle headers are observed before flattening,
using the existing monotone/sticky independent persistence and literal-year parser.
An observation failure stops selected egress; it does not claim other workers
learned an uncommitted hold. No HTTP or sleep occurs under the claim authority lock.

## Rollout, rollback and repair

No rollout is performed by this source change. Quiesce/drain every selected entry
point across API, scheduler and operator workers before deployment; verify all
readers/writers and retained schema before resuming. Active B1/B2 mixed versions
cannot claim B2 coverage. Rollback likewise quiesces those paths, retains both
columns and every hold, and restores only the reverted version's guarantees.

Repair requires separately authorized exact account/owner/current-binding evidence,
healthy clock/schema and quiesced affected egress. There is no automatic repair,
new repair API, schema contraction, retention promise or production activation.
Real PostgreSQL serialization/fault proofs and independent review are required;
SQLite or source assertions alone are not multiworker proof.
