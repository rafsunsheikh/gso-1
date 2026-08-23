#!/usr/bin/env bash
#
# Turn on the phone companion.
#
# Generates an access code, writes it plus the bind address into <repo>/.env,
# and prints the URL to open on the phone. Run it again to rotate the code.
#
# Nothing here exposes GSO-1 to the internet: 0.0.0.0 means "reachable on this
# network", and every non-loopback request must present the code.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO/.env"
PORT="${MANAGER_PORT:-8420}"

# python, not sed -i: the env file holds secrets and must be rewritten whole.
# (Appending with >> once merged two lines into one and corrupted a key.)
new_token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(9))')"

python3 - "$ENV_FILE" "$new_token" <<'PY'
import pathlib, sys

path, token = pathlib.Path(sys.argv[1]), sys.argv[2]
lines = path.read_text().splitlines() if path.exists() else []
wanted = {"MANAGER_MOBILE_TOKEN": token, "MANAGER_HOST": "0.0.0.0"}

out, seen = [], set()
for line in lines:
    key = line.split("=", 1)[0].strip()
    if key in wanted:
        out.append(f"{key}={wanted[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in wanted.items():
    if key not in seen:
        out.append(f"{key}={value}")

path.write_text("\n".join(out) + "\n")
path.chmod(0o600)
PY

# A MANAGER_HOST exported in the shell beats the file we just wrote, and the
# supervisor stamps its own value into every child — so say so now rather than
# letting the phone fail with "cannot connect".
if [ -n "${MANAGER_HOST:-}" ] && [ "$MANAGER_HOST" != "0.0.0.0" ]; then
  cat >&2 <<WARN

  WARNING: MANAGER_HOST=$MANAGER_HOST is exported in this shell.
  An environment variable overrides .env, so GSO-1 will keep binding
  $MANAGER_HOST and the phone will not reach it. Either:

      unset MANAGER_HOST          # then restart GSO-1 from this shell
      export MANAGER_HOST=0.0.0.0 # or set it to match

  Launching GSO-1.app from Finder is unaffected.

WARN
fi

ip="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
host="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"

cat <<EOF

  Phone companion is configured.

    Access code   $new_token
    On your Wi-Fi http://${ip:-<mac-ip>}:$PORT/m
    Or by name    http://$host.local:$PORT/m

  Restart GSO-1 so it binds the network interface:

    python -m supervisor stop && python -m supervisor start --daemon

  Then on the iPhone: open the URL in Safari, enter the code once, and use
  Share -> Add to Home Screen. It runs full-screen from then on.

  The code is stored in $ENV_FILE (chmod 600). Re-run this script to rotate it;
  phones will ask for the new one.

EOF
