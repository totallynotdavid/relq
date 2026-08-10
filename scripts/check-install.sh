#!/usr/bin/env bash
set -euo pipefail

# Exercise built distributions in environments that cannot import this workspace.
check_dir=$(mktemp -d)
trap 'rm -rf "$check_dir"' EXIT

artifact_dir="$check_dir/dist"
sqlite_env="$check_dir/sqlite"
postgres_env="$check_dir/postgres"

uv build --all-packages --out-dir "$artifact_dir"

uv venv --clear --python python "$sqlite_env"
env -u PYTHONPATH uv pip install --python "$sqlite_env/bin/python" \
  --find-links "$artifact_dir" \
  "relq[sqlite]" \
  relq-codegen
env -u PYTHONPATH "$sqlite_env/bin/python" -c '
import importlib.util
import sqlite3

from relq import Column, Table, column, select
from relq_sqlite import SQLiteDatabase

assert importlib.util.find_spec("asyncpg") is None

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
env -u PYTHONPATH uv pip install --python "$postgres_env/bin/python" \
  --find-links "$artifact_dir" \
  "relq[postgres]" \
  "relq-codegen[postgres] @ file://$codegen_wheel"
env -u PYTHONPATH "$postgres_env/bin/python" -c '
import relq
import relq_codegen
import relq_postgres
'
"$postgres_env/bin/relq-codegen" postgres --help >/dev/null
