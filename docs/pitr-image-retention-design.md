# PITR image retention — proposed design

Status: DRAFT / NOT APPLIED. Decision required before implementation or publication.
Baseline: `e20ae90b087816c942ab81410af5f1bc9e80997b` (PR #334).
This document does not enable production PITR, change workflows, grant registry access,
publish images, or change the launch gate (NOT READY). MEMORY is out of scope.

## Evidence and objective

- `ops/pitr/Dockerfile` pins the PostgreSQL base digest, pgBackRest source checksum,
  and APK versions, but downloads packages from a changing Alpine repository.
- `pitr_synthetic.yml` and `pitr_extended.yml` currently build that image locally.
- PR #334 aligned zlib and xz build/runtime packages after repository drift broke
  installation. Exact pins detect drift; they do not preserve downloadable bytes.
- `docs/pitr-policy.md` already acknowledges this availability residual.

Objective: repeat synthetic recovery tests against the same reviewed image without
requiring Alpine packages to remain available. This is artifact reuse, NOT a claim
of byte-reproducible builds or production readiness.

## Options

| Option | Benefit | Remaining limitation |
| --- | --- | --- |
| Build cache only | Faster builds | Eviction/invalidation can require unavailable inputs; not retention |
| Retain tested OCI image by digest (recommended first) | Reuse exact tested image | Registry availability/deletion; future rebuild still needs inputs |
| Archive complete build inputs | Rebuild without upstream package retention | Must preserve base, transitive APKs, signatures/keys, source and toolchain; greater maintenance |

Proposed registry: GHCR with private visibility. This is the planning default, not
authorization to create or publish a package. Namespace, access and retention
ownership must be approved before activation. No registry/package existence or
permission is assumed. Private-package access from CI, including fork PRs, needs
explicit testing; do not expose credentials to untrusted builds or silently skip
required recovery tests when access is unavailable.
Digest references establish content identity, not availability, trusted provenance
or a guarantee that a registry administrator cannot delete content.

## Proposed separation of checks

1. **Source-build check:** keep building the reviewed source without relying on a
   retained-image fallback. Record dependency failures as failures. Never silently
   substitute the last good image and report the new source build as successful.
2. **Candidate proof:** build once for an explicitly supported platform, initially
   linux/amd64; run Synthetic and Extended against the same candidate bytes. Capture
   source SHA, inputs, platform, package inventory, test runs and artifact identity.
3. **Publication gate:** only a separately approved trusted workflow may publish.
   No write credential in PR/fork builds or arbitrary pull_request_target code.
   Grant packages:write only to the publishing job, not the test workflows.
4. **Post-publication proof:** resolve the registry manifest digest, pull that digest
   and repeat both recovery suites against it before changing the consumer lock.
   Do not confuse a local image ID with a registry manifest/index digest.
5. **Consumer-lock PR:** review a literal digest plus evidence record; require both
   source and artifact tests. Consumers must refuse a missing/invalid lock, failed
   pull, wrong platform or digest mismatch. No latest tag or automatic fallback.

The future consumer path must retain a source-build mode for PRs that change the
Dockerfile or baked restore/status scripts; otherwise testing the retained image
would miss those changes. A green retained-image test must not hide a red source
build. Report the two statuses separately.

## Retention, rollback and security

- Keep the current tested digest and at least one previous approved digest. This
  is a proposed minimum, not an applied retention rule or an adequate disaster
  recovery guarantee by itself. No automatic cleanup in the first implementation.
- Before deleting an artifact, inventory all lock consumers and rollback needs;
  deletion requires separate approval. Preserve associated provenance and test evidence.
- A rollback changes only the synthetic consumer lock in a reviewed PR and reruns
  both suites. Never restore a database or switch production as part of this rollback.
- Pinning does not make vulnerabilities disappear. Assign an owner and review cadence
  before activation; rebuild via a reviewed dependency update, test, publish and
  promote a new digest. Urgent security updates follow the same evidence gates.
- Preserve TLS checks, PG16/libpq parity, source checksum verification, synthetic
  marker gates and existing negative recovery cases. No data, WAL, backups, secrets,
  mail headers or personal records belong in the image or publication evidence.
- Scan the proposed image contents and licensing obligations before publication.
  Public repository visibility does not by itself approve public image visibility.

## Later: independently retained inputs

A separate design may retain an OCI export and complete source/build-input closure
with checksums, signature validation and retrieval tests. Define storage location,
access, cost and deletion policy first. A hash list without retained bytes does not
solve availability; preserving an image alone does not prove it can be rebuilt.
Do not copy artifacts into Git or create storage resources under this proposal.

## Implementation slices and acceptance

1. Approve registry/visibility, artifact owner, retention and update cadence.
2. Separate Draft PR: offline lock/provenance validation and negative tests, with
   publication and consumers disabled. Missing digest, mutable tag, wrong platform,
   missing evidence and fallback-to-latest must fail. No invented digest placeholder
   may be accepted as a runnable lock.
3. Separate review/authorization: trusted candidate build/test/publish workflow.
   Demonstrate least required permissions and absence of secrets in output.
4. Explicit publication authorization, then pull-by-digest recovery proof.
5. Separate consumer-lock PR and review; report source-build and retained-image
   recovery separately. Keep production and real Selectel integrations untouched.

STOP if a proposed implementation needs unapproved external writes, new permissions,
public visibility, missing artifacts, unverified provenance or reduced test coverage.

## References

- [GitHub Container registry: permissions and digest pulls](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [Docker build cache backends](https://docs.docker.com/build/cache/backends/)

This proposal is not evidence that any registry, archive, publishing workflow or
retention policy has been configured. Production/deploy remain OFF.
