# Synthetic SSH/S3 custodian v1: source and CI boundary

This separately versioned family is **SOURCE_CI_ONLY**, not a deployment permit.
It does not enable the application's journal, erasure, restoration, API sync,
mail or public access. All test identities, credentials and records are artificial.
The preceding mock-only modules and their source catalogues remain unchanged.

## Fixed operation and trust roots

The OS administrator, root-controlled bootstrap, independent configuration
digest, source catalogue, OS trust store and pinned SSH server key are trusted.
Root compromise, account-owner compromise and a malicious provider are not
solved by placing two services on separate machines in the same cloud account.

The helper has a dedicated SSH credential, not an application/root/backup key.
Its only remote command is the fixed trigger. A root-controlled ForceCommand
passes fixed configuration and its digest to the non-root ingress. SSH input
cannot select paths, Python code, records or S3 credentials. Agent, proxy,
forwarding, PTY, user rc and caller environment are not delegated. Key snapshots
use sealed memory descriptors, not plaintext temporary credential files.

The original systemd controller lifetime, boot identity and invocation own the
admission. It checks that both proposed actor units are absent before creating
the permanent single-operation fence. Existing units are not adopted or stopped.
The new profile never clears the fence to retry. Actor UIDs, groups, capabilities,
source bytes, inode identities, private paths and bounded lifetime are checked
before state/credential access. Teardown closes admission and attempts both
owned groups even after one cleanup error. Uncertain cleanup is not success.

One typed intent is marked DISPATCHED durably before its single conditional PUT.
An independent GET and signed-chain check are required before
COMMITTED_STORAGE_ONLY. Lost PUT acknowledgement or failed GET leaves an
uncertain pending operation, never permission to purge, restore or retry.

The separate live store fixes the ru-3 endpoint, canonical public IPv4 and
request operation. TCP uses that pinned IPv4 while Host, SNI and mandatory
certificate hostname verification retain the provider hostname. It has no
caller-supplied client, transport, TLS, DNS, proxy, redirect or retry option.
The writer uses `If-None-Match: *`; only an actually received and closed HTTP412
is classified as a conflict. Neither HTTP200 nor an exception is a durable ACK.

## Evidence boundaries

| CI gate | What is actually exercised | What it does not establish |
| --- | --- | --- |
| Portable components | Parsing, source closure, signing, fixed transport options and fault contracts; 49 Windows/92 Linux tests | Actual provider, OpenSSH or manager isolation |
| Native OpenSSH | Real server/client in owned mount/PID/network namespaces; two positives and seven denials; kernel UID/capability observations and cleanup | Storage action, real S3 or full combined two-host chain |
| Native storage | Real systemd/UID/Lease/SQLite/Unix IPC; happy, lost PUT ACK and failed GET; actual trace/fault activation and whole-group cleanup | Real provider, TLS, IAM, or SSH in this fixture |

The storage fixture uses an explicitly named, signed Unix mock S3 adapter.
Test adapters live only under `ops/backup/tests/`; runtime has no mock switch.
OpenSSH .debs are fetched at exact SHA256/size and unpacked into an owned
namespace fixture; package installation and maintainer scripts are forbidden.
Seven wheel artifacts retain the predecessor's exact hash lock. Neither binary
artifacts nor generated receipts belong in Git. Optimized proof entry is refused.

Normal OpenSSH may leave a nonzero capability bounding ceiling and the primary
GID in the supplementary list. Ingress requires no-new-privileges and zero
effective/permitted/inheritable/ambient capabilities; it accepts only that exact
primary GID or an empty supplementary list. Writer/custodian actors still require
all five capability sets to be zero. This is a measured boundary, not sudo
delegation to the SSH account.

## Gates still required before independent-host acceptance

Fresh provider quote, quota, balance, availability and least-privilege inventory
must precede any paid provisioning. The owner-approved additional monthly cap
must include VM, disk, IP, network and synthetic S3 reserve. No CI workflow
creates cloud resources or receives cloud secrets.

Actual separate-host SSH plus verified TLS, S3 conditional-write and IAM denial
tests, network-filter enforcement, trusted cold boot, crash/old-copy/limit proofs
and a bounded resource/cost/cleanup inventory remain independent gates. Their
absence must never be relabelled as a full-chain PASS. These source tests alone
do not authorize client data, a working journal or public service launch.
