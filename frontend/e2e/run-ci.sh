#!/usr/bin/env bash
# GitHub-hosted Linux only. Install dependencies BEFORE entering this namespace.
set -euo pipefail

python - <<'PY'
import os
from pathlib import Path
db = Path(os.environ["E2E_DB_PATH"]).resolve()
root = db.parent.parent
assert root.parent == Path(os.environ["RUNNER_TEMP"]).resolve()
assert root.name.startswith("pult-mvp-e2e.")
assert db.parent.name == "data" and db.name == "e2e.sqlite" and not db.exists()
assert os.getuid() != 0, "browser must run as the runner user"
PY

PYTHON_LIBDIR="$(python -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
test -d "$PYTHON_LIBDIR"

# Only root brings up lo; drop UID/GID, all capability sets and supplementary
# groups before executing any application code. No inherited GitHub credentials.
# This confines ordinary network I/O, not a sandbox for hostile OS-level code.
exec sudo -n /usr/bin/env -i \
  PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 LD_LIBRARY_PATH="$PYTHON_LIBDIR" \
  E2E_DB_PATH="$E2E_DB_PATH" CI=true APP_ENV=development \
  SMTP_HOST= SMTP_USER= SMTP_PASSWORD= SENTRY_DSN= CRED_ENC_KEY= \
  BILLING_ENABLED=false NEXT_PUBLIC_BILLING_ENABLED=false \
  API_DATA_SYNC_ENABLED=false AUTOMATION_ENABLED=false \
  NEXT_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 PYTHONDONTWRITEBYTECODE=1 NPM_CONFIG_OFFLINE=true \
  /usr/bin/unshare --net -- /bin/bash --noprofile --norc -euo pipefail -c '
    /usr/sbin/ip link set lo up
    test "$(/usr/sbin/ip -o link show | /usr/bin/wc -l)" -eq 1
    exec /usr/bin/setpriv --reuid="$1" --regid="$2" --clear-groups \
      --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs \
      /bin/bash --noprofile --norc -euo pipefail
  ' _ "$(id -u)" "$(id -g)" <<'E2E_SCRIPT'
python - <<'PY'
import errno
import os
from pathlib import Path
import socket
assert os.getuid() != 0
interfaces = [line.split(":", 1)[0].strip() for line in Path("/proc/net/dev").read_text().splitlines()[2:]]
assert interfaces == ["lo"], interfaces
with socket.socket() as probe:
    probe.settimeout(0.3)
    assert probe.connect_ex(("192.0.2.1", 9)) == errno.ENETUNREACH
print("E2E_LOOPBACK_NAMESPACE_ACTIVE", flush=True)
PY
result=0
npm run test:e2e -- e2e/mvp-path.spec.ts --project=chromium --retries=0 --forbid-only --global-timeout=600000 --trace=off </dev/null || result=$?
# Check INSIDE the same namespace after Playwright's teardown, including failures.
python - <<'PY' || result=1
import socket
for port in (8099, 3099):
    with socket.socket() as probe:
        probe.settimeout(0.3)
        assert probe.connect_ex(("127.0.0.1", port)) != 0, f"E2E server still listening: {port}"
PY
exit "$result"
E2E_SCRIPT
