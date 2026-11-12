#!/bin/sh
# Blocking foundation only: no replay/deletion or application-release authorization.
set -eu
LC_ALL=C; export LC_ALL
fail() { printf 'erasure checkpoint: BLOCKED: %s\n' "$1" >&2; exit 1; }
[ "$#" = 1 ] || fail "one private snapshot required"
: "${RESTORE_ERASURE_CHECKPOINT_SHA256:?trusted external digest required}"
: "${RESTORE_ERASURE_EXPECTED_SCOPE:?trusted external scope required}"
: "${RESTORE_ERASURE_EXPECTED_SEQUENCE:?trusted exact head sequence required}"
: "${RESTORE_ERASURE_EXPECTED_CIPHER_SHA256:?trusted backup digest required}"

# Pins must be obtained independently of the backup and checkpoint transport.
for hash in "$RESTORE_ERASURE_CHECKPOINT_SHA256" "$RESTORE_ERASURE_EXPECTED_CIPHER_SHA256"; do
    [ "${#hash}" = 64 ] || fail "invalid digest pin"
    case "$hash" in *[!0-9a-f]*) fail "invalid digest pin";; esac
done
case "$RESTORE_ERASURE_EXPECTED_SCOPE" in ''|*[!a-zA-Z0-9_-]*) fail "invalid scope pin";; esac
[ "${#RESTORE_ERASURE_EXPECTED_SCOPE}" -le 64 ] || fail "invalid scope pin"
case "$RESTORE_ERASURE_EXPECTED_SEQUENCE" in ''|0*|*[!0-9]*) fail "invalid sequence pin";; esac
[ "${#RESTORE_ERASURE_EXPECTED_SEQUENCE}" -le 15 ] || fail "invalid sequence pin"
[ -f "$1" ] && [ ! -L "$1" ] || fail "snapshot not a regular file"
SIZE="$(wc -c < "$1" | tr -d ' ')"
[ "$SIZE" -le 65536 ] || fail "checkpoint too large"
GOT="$(sha256sum "$1")" || fail "cannot hash checkpoint"
GOT="${GOT%% *}"
[ "$GOT" = "$RESTORE_ERASURE_CHECKPOINT_SHA256" ] || fail "checkpoint digest mismatch"

# Exact five-line envelope. Extra records (valid or malformed) NEVER permit restore.
# A complete empty set is not an empty batch. No IDs/contents are emitted to logs.
if cmp -s "$1" - <<EOF
pult-erasure-checkpoint-v1
scope $RESTORE_ERASURE_EXPECTED_SCOPE
sequence $RESTORE_ERASURE_EXPECTED_SEQUENCE
backup-sha256 $RESTORE_ERASURE_EXPECTED_CIPHER_SHA256
coverage complete
EOF
then
    printf 'erasure checkpoint: EMPTY_CHECKPOINT_QUARANTINE_ONLY\n'
else
    fail "invalid or nonempty checkpoint; reconciliation required"
fi
