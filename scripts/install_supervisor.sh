#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/workspace/fb-avatar-live}"
[[ -f "$ROOT/.env" ]] || { echo "Missing $ROOT/.env" >&2; exit 1; }

cat >/opt/supervisor-scripts/fb-avatar-musetalk.sh <<EOF
#!/usr/bin/env bash
utils=/opt/supervisor-scripts/utils
. "\${utils}/logging.sh"
. "\${utils}/environment.sh"
exec "$ROOT/scripts/run_musetalk.sh"
EOF

cat >/opt/supervisor-scripts/fb-avatar-pipeline.sh <<EOF
#!/usr/bin/env bash
utils=/opt/supervisor-scripts/utils
. "\${utils}/logging.sh"
. "\${utils}/environment.sh"
exec "$ROOT/scripts/run_pipeline.sh"
EOF

chmod +x \
  /opt/supervisor-scripts/fb-avatar-musetalk.sh \
  /opt/supervisor-scripts/fb-avatar-pipeline.sh \
  "$ROOT/scripts/run_musetalk.sh" \
  "$ROOT/scripts/run_pipeline.sh"

cat >/etc/supervisor/conf.d/fb-avatar-musetalk.conf <<'EOF'
[program:fb-avatar-musetalk]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/fb-avatar-musetalk.sh
autostart=true
autorestart=true
startsecs=10
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_logfile_maxbytes=0
priority=20
EOF

cat >/etc/supervisor/conf.d/fb-avatar-pipeline.conf <<'EOF'
[program:fb-avatar-pipeline]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/fb-avatar-pipeline.sh
autostart=true
autorestart=true
startsecs=10
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_logfile_maxbytes=0
priority=30
EOF

supervisorctl reread
supervisorctl update
supervisorctl status fb-avatar-musetalk fb-avatar-pipeline
