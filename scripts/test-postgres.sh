#!/usr/bin/env bash
# Run PostgreSQL integration tests against an isolated local server.

set -euo pipefail

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
postgres_bin="$(mise where postgres)/bin"
postgres_data="$(mktemp -d)"
postgres_socket="$(mktemp -d)"
postgres_port=55435

cleanup() {
  "$postgres_bin/pg_ctl" -D "$postgres_data" -m immediate stop >/dev/null 2>&1 || true
  rm -rf "$postgres_data" "$postgres_socket"
}
trap cleanup EXIT

"$postgres_bin/initdb" --auth=trust --username=relq --no-instructions -D "$postgres_data" >/dev/null
"$postgres_bin/pg_ctl" -D "$postgres_data" -w start -o "-c listen_addresses='' -k $postgres_socket -p $postgres_port" >/dev/null
"$postgres_bin/createdb" -h "$postgres_socket" -p "$postgres_port" -U relq relq

cd "$repository_root"
RELQ_TEST_POSTGRES_DSN="postgresql://relq@/relq?host=$postgres_socket&port=$postgres_port" \
  uv run --locked --all-packages pytest tests/integration/postgres
