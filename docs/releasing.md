# Releasing

Each release publishes one tested version set: `relq`, `relq-sqlite`,
`relq-postgres`, `relq-codegen`, and `relq-migrate` all have the same version.
Pushing a `v*` tag is the only thing that starts publishing.

## Prepare a release

1. Update all five package versions and their exact cross-package dependencies.
2. Rewrite `.github/release-notes.md` as a short, direct summary for users. Its
   first line must be `relq X.Y.Z is out.` for the version you are releasing, or
   the Release workflow stops before it builds. The workflow uses the file as
   the GitHub Release body, and GitHub appends its generated change list and
   comparison link.
3. Run `mise check`, commit the release preparation, and merge it.

## Sign and publish the tag

Create the tag from the merged commit using your configured signing key:

```bash
git tag -s vX.Y.Z -m "Release vX.Y.Z"
git verify-tag vX.Y.Z
git push origin vX.Y.Z
```

The tag starts the Release workflow, `.github/workflows/release.yml`. It
verifies the complete version set, builds and smoke-tests the artifacts,
publishes them to PyPI, then creates the GitHub Release with the same artifacts.
