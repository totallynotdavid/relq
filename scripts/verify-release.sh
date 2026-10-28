#!/usr/bin/env bash
# Check that the release tag, the distribution versions and the release notes agree.
# Reads the tag from RELEASE_TAG, for example v0.1.0. Without it, checks the
# release notes against the current version, as a pull request does.

set -euo pipefail

cd "$(dirname -- "${BASH_SOURCE[0]}")/.."

: "${RELEASE_TAG:=v$(uv run --locked --all-packages python -c 'from importlib.metadata import version; print(version("relq"))')}"
export RELEASE_TAG

uv run --locked --all-packages python -c '
from importlib.metadata import version
from os import environ

tag = environ["RELEASE_TAG"].removeprefix("v")
versions = {
    version(name)
    for name in ("relq", "relq-sqlite", "relq-postgres", "relq-codegen", "relq-migrate")
}
assert versions == {tag}, f"tag {tag!r} does not match distribution versions {versions!r}"
'

expected="relq ${RELEASE_TAG#v} is out."
first_line=$(head -n 1 .github/release-notes.md)
if [ "$first_line" != "$expected" ]; then
  echo "first line of .github/release-notes.md is '$first_line', expected '$expected'" >&2
  exit 1
fi
