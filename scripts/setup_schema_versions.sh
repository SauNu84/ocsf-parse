#!/usr/bin/env bash
# Materialize pinned OCSF schema versions as git worktrees of the
# ocsf-schema submodule. Worktrees share the submodule's .git directory,
# so disk impact is small (~a few MB per version, not a full clone).
#
# Run after `git submodule update --init --recursive`. Idempotent —
# re-running skips any version that's already materialized.
#
# Add new versions by appending to SCHEMA_VERSIONS below: <version>:<git-ref>.

set -euo pipefail

SCHEMA_VERSIONS=(
  "1.8.0:3dcb905d"    # v1.8.0 prep — version.json: 1.8.0
  "1.7.0:dc6359b4"    # v1.7.0 Release Prep — version.json: 1.7.0
)

cd "$(dirname "$0")/.."

if [ ! -d ocsf-schema/.git ] && [ ! -f ocsf-schema/.git ]; then
  echo "error: ocsf-schema/ submodule not initialised. Run:" >&2
  echo "  git submodule update --init --recursive" >&2
  exit 1
fi

for entry in "${SCHEMA_VERSIONS[@]}"; do
  version="${entry%%:*}"
  ref="${entry##*:}"
  dir="ocsf-schema-${version}"
  if [ -d "$dir" ]; then
    echo "✓ $dir already present — skipping."
    continue
  fi
  echo "materialising $dir at $ref…"
  git -C ocsf-schema worktree add "../$dir" "$ref"
done

echo
echo "Pinned schema versions ready:"
for entry in "${SCHEMA_VERSIONS[@]}"; do
  version="${entry%%:*}"
  printf "  %-12s %s\n" "$version" "ocsf-schema-${version}/"
done
