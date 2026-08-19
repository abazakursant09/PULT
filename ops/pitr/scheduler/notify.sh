#!/bin/sh
# SECURITY-2D-3E1B-3C3-A — DORMANT alert dispatch STUB. This PR performs NO real network alert: it only
# writes an allowlisted, secret-free line to stdout / a local log. A real transport (webhook/e-mail/SMS)
# is added ONLY at Inal-approved activation (separate PR), never here. If a webhook is configured to
# anything other than a placeholder, this refuses to send, so an accidental real endpoint cannot fire.
. "$(dirname "$0")/_lib.sh"
pitr_require_dormant_gate
severity="${1:-unknown}"
signal="${2:-unknown}"
case "$severity" in warning|critical|emergency|unknown) : ;; *) severity=unknown ;; esac
_wh="${ALERT_WEBHOOK:-}"
# A real send is intentionally NOT implemented in 3C3-A. Guard against a real endpoint being present.
case "$_wh" in
    ""|*REPLACE*|*.invalid) : ;;   # placeholder / unset => log-only (expected dormant state)
    *) pitr_log "pitr_alert=refuse reason=real_transport_not_allowed_in_3C3A"; exit 5 ;;
esac
pitr_log "pitr_alert severity=$severity signal=$signal transport=log-only"
