#!/bin/bash
set -e

# =============================================================================
# PgBouncer entrypoint — generates userlist.txt from environment variables
# =============================================================================

USERLIST_PATH="/etc/pgbouncer/userlist.txt"

# Generate md5 hash: md5(password + username)
# Format: "username" "md5<hash>"
generate_userlist() {
    local username="${POSTGRES_USER:-postgres}"
    local password="${POSTGRES_PASSWORD:-postgres}"

    # md5(password || username) — PostgreSQL md5 auth format
    local hash=$(echo -n "${password}${username}" | md5sum | awk '{print $1}')

    echo "\"${username}\" \"md5${hash}\"" > "${USERLIST_PATH}"
    echo "Generated userlist for user: ${username}"
}

# Generate userlist before starting PgBouncer
generate_userlist

# Start PgBouncer with the generated configuration
exec gosu pgbouncer /usr/bin/pgbouncer /etc/pgbouncer/pgbouncer.ini
