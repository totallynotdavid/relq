relq 0.2.0 is out.

This release removes relq's runtime dependency on `typing-extensions`. It has no
breaking changes.

The rest is build, CI and dependency work.

## Known problems

None known.

## What to do

Upgrade the distributions together:

```bash
uv add "relq[sqlite]==0.2.0"
uv add "relq[postgres]==0.2.0"
uv add "relq-migrate[codegen]==0.2.0"
```

Run your test suite after upgrading. Report problems at
https://github.com/totallynotdavid/relq/issues.
