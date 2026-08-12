# Base image digest pinning policy (SECURITY-2D-3E1)

Every production application base image is referenced as `tag@sha256:<digest>`:

| Dockerfile | Reference |
|------------|-----------|
| `backend/Dockerfile` | `python:3.11-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff` |
| `frontend/Dockerfile` | `node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293` |

The readable tag stays for humans; the immutable digest freezes the exact bytes. Both
digests are OCI image-index (manifest-list) digests whose `linux/amd64` sub-manifest is
what BuildKit selects on the amd64 CI runner and the amd64 deploy target.

## Rules

- **Production references are always `tag@sha256:<digest>`.** A tag-only, digest-only, or
  `latest` reference is rejected by `backend/tests/test_base_image_digest_pin_guard.py`.
- **A digest changes only in a separate, reviewed PR.** The digest bump is never mixed
  with a Python or npm dependency upgrade, or with any other change.
- **The PR description records the full old → new digest** for every changed image, plus
  the date and the reason for the bump. This history lives in the PR, **not** in Dockerfile
  comments.
- **Before a bump, verify against the authoritative registry**: tag ownership, that the
  index still contains a `linux/amd64` manifest, and that the amd64 config still reports
  the intended OS family and Python/Node version. Use two independent sources (Docker
  Registry v2 API and Docker Hub API / `docker buildx imagetools inspect`).
- **CI must stay green on the new digest**: Docker Build (build directly on the digest),
  the backend and frontend non-root + writable-allowlist checks, and the frontend HTTP
  smoke are all mandatory. Images are built with `push: false` (never published here).
- **A bot (Dependabot/Renovate) may only open a PR — auto-merge is forbidden.** No such bot
  is configured in 3E1; adding one is a separate, later decision.

## PostgreSQL (`postgres:16-alpine`) — pinned by digest

`docker-compose.yml` references the `postgres:16-alpine` stateful data-service image (which
this repository does not build). Its major/variant was aligned to the CI-proven canonical in
SECURITY-2D-3E1B-1, and it is now **digest-pinned** in SECURITY-2D-3E1B-2 to
`postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777`
(the OCI index digest the green master CI ran — PostgreSQL 16.14, Alpine, linux/amd64). The
full policy, verification, and digest-bump procedure live in `docs/postgresql-version-policy.md`.

Still open (tracked, **not** implemented): automated backup / PITR / verified restore =
**3E1B-3**. A digest pin freezes bytes and guarantees reproducibility; it is **not** a CVE
audit, and deployment is **not** fully immutable and disaster recovery is **not** in place
until 3E1B-3 lands.
