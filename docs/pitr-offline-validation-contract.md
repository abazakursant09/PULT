# PITR offline validation contract

DRAFT / NOT APPLIED. Design only; no executable lock, registry access, publication,
workflow change or production activation. Launch gate remains NOT READY.
Parent proposal: [image retention design](pitr-image-retention-design.md).

## Boundary and outcome

The proposed validator checks a local candidate record against a separately supplied
review policy. It never pulls an image, contacts GitHub, runs Docker, executes input,
reads credentials or changes files. No new Python dependencies are needed.

Success means `STRUCTURE_AND_BINDING_VALID`, never `TRUSTED`, `PUBLISHED`,
`MERGE_SAFE` or `PRODUCTION_READY`. A forged but internally consistent record can
pass offline checks. Authenticity and actual test outcomes require a separate
trusted online verification gate before any image can be consumed.

## Proposed interface

Single local Python entry point under `ops/pitr/`, with a separately testable pure
validation function. Inputs: candidate JSON file and explicit expected repository,
source commit, image repository and platform from reviewed policy/CLI arguments.
Do not derive these expectations from the candidate itself. No default real image
repository until namespace approval; no production consumer in this slice.

Exit 0 only for the narrow success above; exit nonzero for malformed, unavailable,
oversized or contradictory inputs. Output a short category, not raw input or
exception text that could disclose accidentally supplied secrets.

## Candidate v1 record (proposed, not a runnable fixture)

- Exact top-level keys: schema_version, source_repository, source_commit,
  image_repository, image_digest, platform, evidence.
- schema_version: integer 1, not a JSON boolean.
- source_repository: exact match to the separately supplied expected repository.
- source_commit: full lowercase 40-character Git SHA, matching expected commit.
- image_repository: exact policy match; proposed GHCR repository path only, with
  no scheme, credentials, whitespace, query, fragment, tag or appended digest.
- image_digest: literal `sha256:` followed by 64 lowercase hexadecimal characters;
  reject all-zero sentinel. Syntactic validity does not prove content exists.
- platform: exactly linux/amd64 in v1 and equal to policy.
- evidence: exactly two named entries, synthetic and extended. Each contains exact
  keys: source_commit, image_digest, phase, run_id, run_attempt, conclusion.
- Each evidence entry must bind to the candidate commit and registry digest;
  phase must be post_publication; conclusion must be success; run_id and run_attempt
  must be positive integers (not booleans). Online verification must independently
  confirm these assertions and bind each run to its expected workflow/job.

Do not accept missing, extra or duplicate JSON keys, nulls, implicit coercion,
case folding, trailing JSON data, NaN or Infinity. Cap file input at 64 KiB before
parsing and handle excessive nesting as invalid input. Read UTF-8 strictly.
Do not follow a path supplied inside the record; the schema contains no such path.

## Negative and safe tests required before implementation review

1. Missing/extra/duplicate keys at every object level; invalid JSON and encoding;
   input over limit; excessive nesting; wrong types including bool-as-int.
2. Missing digest; mutable tag; truncated/uppercase/nonhex/all-zero digest;
   repository with credentials, URL syntax, tag or digest suffix.
3. Wrong expected repository, commit, image repository or platform. Expectations
   must come from outside the candidate and cannot be overridden by its fields.
4. Missing either evidence entry; stale commit/digest; pre-publication phase;
   pending/skipped/failure conclusion; invalid run identity/attempt.
5. No fallback to latest, previous image, network or environment credentials on
   any error. Error output must not echo malicious input or tracebacks.
6. Valid synthetic test records pass; JSON indentation and object-key ordering
   do not change results. Test-only fake hashes remain confined to test fixtures:
   no runnable consumer lock is installed by these tests.
7. An internally consistent forged record is explicitly demonstrated as a limit
   of offline checks; success text must retain the narrow semantic label.

## Separate future trust gate (not implemented here)

Before consumption, independently authenticate provenance, verify actual registry
manifest/platform and both workflow runs on the expected source and pulled digest,
check workflow identity and trust context, and record a reviewed promotion decision.
Run IDs or a self-reported success string are not evidence of success by themselves.
Neither a registry tag nor a local Docker image ID substitutes for the manifest digest.

Publication remains separately authorized. Inal approved private GHCR associated
with `abazakursant09/PULT`, Inal as owner, current/previous image retention without
automatic deletion, monthly update review and out-of-cycle critical vulnerability
review (2026-10-10). These are planning decisions, not applied registry settings.
Exact package path and private-registry/fork access still require verification.
The offline slice must not need registry credentials or create resources implicitly.

## Implementation acceptance and stop

Future separate implementation scope: validator plus focused tests, and a minimal
usage note only if needed. No workflow wiring, executable lock, packages:write,
network dependency, registry resource, MEMORY change or production change.
Run focused tests, negative tests and lint; use a separate Draft PR and review.
Do not label that implementation a complete provenance verifier.
