#!/bin/sh
set -eu

# Docker creates missing bind-mount directories as root on Linux. Prepare only
# the recording directory, then run Python as the configured unprivileged UID.
if [ "$(id -u)" = 0 ]; then
    logger_uid="${LOGGER_UID:-1000}"
    logger_gid="${LOGGER_GID:-1000}"
    case "$logger_uid" in
        ''|*[!0-9]*|0) echo 'LOGGER_UID must be a positive integer' >&2; exit 2 ;;
    esac
    case "$logger_gid" in
        ''|*[!0-9]*|0) echo 'LOGGER_GID must be a positive integer' >&2; exit 2 ;;
    esac
    mkdir -p "${LOG_DIR:-/data}"
    chown "$logger_uid:$logger_gid" "${LOG_DIR:-/data}"
    exec gosu "$logger_uid:$logger_gid" "$@"
fi

exec "$@"
