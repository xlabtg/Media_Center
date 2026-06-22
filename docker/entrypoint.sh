#!/bin/sh
set -eu

if [ "${1:-serve}" != "serve" ]; then
    exec "$@"
fi

if [ "$#" -gt 0 ]; then
    shift
fi

service_name="${SERVICE_NAME:-service}"
app_host="${APP_HOST:-0.0.0.0}"
app_port="${APP_PORT:-7700}"
log_level="${LOG_LEVEL:-info}"

if [ -n "${APP_MODULE:-}" ]; then
    app_module="$APP_MODULE"
else
    service_package="$(printf "%s" "$service_name" | tr "-" "_")"
    app_module="${service_package}_app.main:app"
fi

case "$app_module" in
    *:*) ;;
    *) app_module="${app_module}:app" ;;
esac

echo "[entrypoint] Starting ${service_name} via ${app_module} on ${app_host}:${app_port}"
exec uvicorn "$app_module" \
    --host "$app_host" \
    --port "$app_port" \
    --log-level "$log_level" \
    "$@"
