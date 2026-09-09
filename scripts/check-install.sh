#!/usr/bin/env bash
set -euo pipefail

# Exercise release artifacts in environments that cannot import this workspace.
if [ "$#" -ne 1 ]; then
  echo "usage: $0 ARTIFACT_DIR" >&2
  exit 2
fi

artifact_dir=$(cd "$1" && pwd -P)
if ! compgen -G "$artifact_dir/*.whl" >/dev/null; then
  echo "artifact directory contains no wheels: $artifact_dir" >&2
  exit 1
fi

check_dir=$(mktemp -d)
trap 'rm -rf "$check_dir"' EXIT

sqlite_env="$check_dir/sqlite"
postgres_env="$check_dir/postgres"
cd "$check_dir"

uv venv --clear --python python "$sqlite_env"
env -u PYTHONPATH uv pip install --reinstall --python "$sqlite_env/bin/python" \
  --find-links "$artifact_dir" \
  "relq[sqlite]" \
  relq-codegen \
  relq-migrate
env -u PYTHONPATH "$sqlite_env/bin/python" -c '
import importlib.util
import sqlite3

from relq import Column, Table, column, select
from relq.postgres import cast_uuid, json_text, regex_match
from relq_migrate import SQLiteMigrator
from relq_sqlite import SQLiteDatabase

# relq.postgres is a compiler-side expression surface, not a driver binding.
assert importlib.util.find_spec("asyncpg") is None
assert (cast_uuid, json_text, regex_match)
assert SQLiteMigrator.__name__ == "Migrator"
namespace = {}
exec("from relq_migrate import *", namespace)
assert "PostgresMigrator" not in namespace

class Numbers(Table):
    value: Column[int] = column(int)

numbers = Numbers("numbers")
connection = sqlite3.connect(":memory:")
connection.execute("create table numbers (value integer not null)")
connection.execute("insert into numbers values (7)")
assert SQLiteDatabase(connection).fetch_all(select(numbers.value).from_(numbers)) == [(7,)]
'
"$sqlite_env/bin/python" -c '
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.execute("create table users (id integer primary key, email text not null)")
connection.close()
' "$check_dir/schema.db"
"$sqlite_env/bin/relq-codegen" sqlite "$check_dir/schema.db" "$check_dir/schema.py"
"$sqlite_env/bin/python" -m py_compile "$check_dir/schema.py"

uv venv --clear --python python "$postgres_env"
codegen_wheel=$(printf '%s' "$artifact_dir"/relq_codegen-*.whl)
env -u PYTHONPATH uv pip install --reinstall --python "$postgres_env/bin/python" \
  --find-links "$artifact_dir" \
  "relq[postgres]" \
  "relq-codegen[postgres] @ file://$codegen_wheel" \
  "relq-migrate[postgres]"
env -u PYTHONPATH "$postgres_env/bin/python" -c '
import relq
import relq_codegen
from relq_migrate import PostgresMigrator
import relq_postgres

assert PostgresMigrator.__name__ == "Migrator"
'
"$postgres_env/bin/relq-codegen" postgres --help >/dev/null
