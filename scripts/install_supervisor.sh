#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/workspace/fb-avatar-live}"
[[ -f "$ROOT/.env" ]] || cp "$ROOT/.env.example" "$ROOT/.env"
chmod 600 "$ROOT/.env"

make_wrapper() {
  local name="$1" script="$2"
  cat >"/opt/supervisor-scripts/${name}.sh" <<EOF
#!/usr/bin/env bash
utils=/opt/supervisor-scripts/utils
. "\${utils}/logging.sh"
. "\${utils}/environment.sh"
exec "$ROOT/scripts/$script"
EOF
  chmod +x "/opt/supervisor-scripts/${name}.sh"
}

make_wrapper fb-avatar-mediamtx run_mediamtx.sh
make_wrapper fb-avatar-musetalk run_musetalk.sh
make_wrapper fb-avatar-pipeline run_pipeline.sh
make_wrapper fb-avatar-forwarder run_forwarder.sh

cat >/opt/supervisor-scripts/fb-avatar-control.sh <<EOF
#!/usr/bin/env bash
utils=/opt/supervisor-scripts/utils
. "\${utils}/logging.sh"
. "\${utils}/environment.sh"
. "\${utils}/exit_portal.sh" "Avatar Live Studio"
exec "$ROOT/scripts/run_control_panel.sh"
EOF
chmod +x /opt/supervisor-scripts/fb-avatar-control.sh "$ROOT"/scripts/*.sh

write_service() {
  local name="$1" priority="$2" startsecs="$3"
  cat >"/etc/supervisor/conf.d/${name}.conf" <<EOF
[program:${name}]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/${name}.sh
autostart=true
autorestart=unexpected
startsecs=${startsecs}
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_logfile_maxbytes=0
stopasgroup=true
killasgroup=true
priority=${priority}
EOF
}

write_service fb-avatar-mediamtx 10 3
write_service fb-avatar-control 15 3
write_service fb-avatar-musetalk 20 10
write_service fb-avatar-pipeline 30 10
write_service fb-avatar-forwarder 40 3

/venv/main/bin/python - <<'PY'
import yaml
path = "/etc/portal.yaml"
with open(path, encoding="utf-8") as stream:
    data = yaml.safe_load(stream) or {"applications": {}}
data.setdefault("applications", {})["Avatar Live Studio"] = {
    "hostname": "localhost",
    "external_port": 10200,
    "internal_port": 18080,
    "open_path": "/",
    "name": "Avatar Live Studio",
}
with open(path, "w", encoding="utf-8") as stream:
    yaml.safe_dump(data, stream, sort_keys=False)
PY

supervisorctl reread
supervisorctl update
supervisorctl restart caddy
supervisorctl status fb-avatar-mediamtx fb-avatar-control fb-avatar-musetalk fb-avatar-pipeline fb-avatar-forwarder || true
