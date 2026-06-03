# Release checklist

How to cut a release of `ocsf-parse`. Two sections:

1. **One-time setup** — gates that need to be flipped in the GitHub
   and PyPI web UIs before the workflows in `.github/workflows/` can
   actually do their job. Once done, never touch again.
2. **Per-release recipe** — the commands you actually run for every
   subsequent tag.

If a step here is wrong, the failure mode is usually a workflow run
that goes green-then-red, not a silent regression. Check the Actions
tab on GitHub and look for the failing job.

---

## 1. One-time setup

Do these once, in any order. They unblock everything downstream.

### 1.1 GitHub Pages (landing page)

The `pages.yml` workflow deploys `docs/` to GitHub Pages on every
push to `main` that touches the `docs/` folder.

1. https://github.com/SauNu84/ocsf-parse/settings/pages
2. **Source** → "GitHub Actions"
3. Save.

That's it. The next time `docs/` changes (or you manually dispatch
the `pages` workflow), the site goes live at:

    https://saunu84.github.io/ocsf-parse/

**Verify**: trigger the workflow manually from the Actions tab,
confirm the URL renders.

### 1.2 PyPI Trusted Publisher

The `publish.yml` workflow publishes to PyPI without an API token,
using OIDC. PyPI needs to know which workflow on which repo is
allowed to publish under which project name.

1. **Make sure you have a PyPI account.** If not:
   https://pypi.org/account/register/
2. **Add the trusted publisher.** While the package doesn't yet exist
   on PyPI, you register a *pending* publisher — PyPI promotes it to
   a real one on first successful publish.
   - https://pypi.org/manage/account/publishing/
   - Click "Add a new pending publisher".
   - PyPI Project Name: **`ocsf-mapper`**
   - Owner: **`SauNu84`**
   - Repository: **`ocsf-parse`**
   - Workflow filename: **`publish.yml`**
   - Environment name: **`pypi`**
3. **Create the matching GitHub environment.**
   - https://github.com/SauNu84/ocsf-parse/settings/environments
   - Click "New environment", name it **`pypi`**.
   - Optional but recommended: "Deployment branches and tags" →
     "Selected branches and tags" → add a rule for `v*` tags. Then
     only the `v0.3.0` (etc.) tag push can trigger publishing.

**Verify**: tag a release (see §2). The `publish.yml` workflow's
`publish` job should succeed and `pip install ocsf-mapper` should
work within a few minutes.

If the first publish fails with `403 forbidden`, the pending
publisher slot didn't match the actual workflow run. Check that
the *workflow filename* on PyPI matches the file in
`.github/workflows/`, and the *environment name* matches your
GitHub environment.

### 1.3 GHCR (container registry)

The `docker.yml` workflow pushes to `ghcr.io/saunu84/ocsf-mapper`
using the auto-injected `GITHUB_TOKEN` — no setup needed for the
push itself. But the resulting package is private by default.

To make `docker pull ghcr.io/saunu84/ocsf-mapper:latest` work
without authentication:

1. After the first tag push, the package shows up at:
   https://github.com/users/SauNu84/packages/container/ocsf-mapper
2. Click "Package settings" → "Change package visibility" → **Public**.

(Alternative if you want it kept private: leave it as is and
document `docker login ghcr.io` in your install instructions.)

---

## 2. Per-release recipe

Once §1 is done, every subsequent release looks like this. Replace
`X.Y.Z` with the actual version (e.g. `0.3.0`).

### 2.1 Update version + CHANGELOG

```bash
# Bump the two version constants.
sed -i '' 's/version = "[^"]*"/version = "X.Y.Z"/' pyproject.toml
sed -i '' 's/__version__ = "[^"]*"/__version__ = "X.Y.Z"/' src/ocsf_mapper/__init__.py
```

Manually edit `CHANGELOG.md`:

- Change the existing "Unreleased" heading to the actual release
  date: `## X.Y.Z — YYYY-MM-DD`.
- Add a fresh `## X.Y.Z+1 — Unreleased` section above it for the
  next cycle.

### 2.2 Final sanity check

```bash
python3 -m pytest -q             # all green
python3 -m ocsf_mapper.lint mappings/   # exits 0
python3 -m build                  # builds sdist + wheel locally
```

The CI workflow does these same checks; running them locally just
avoids waiting on a failed CI run.

### 2.3 Commit, tag, push

```bash
git add pyproject.toml src/ocsf_mapper/__init__.py CHANGELOG.md
git commit -m "release: vX.Y.Z"

git tag -a vX.Y.Z -m "vX.Y.Z — <short summary>"
git push origin main
git push origin vX.Y.Z
```

### 2.4 What fires automatically

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | push to main | pytest + lint on Python 3.9 / 3.11 / 3.12 |
| `publish.yml` | tag push `v*` | sdist + wheel → PyPI |
| `docker.yml` | tag push `v*` + main push | container image → GHCR |
| `pages.yml` | push to main if `docs/**` changed | landing page → GitHub Pages |

All four are visible on https://github.com/SauNu84/ocsf-parse/actions
within ~30 seconds of the push.

#### If `publish.yml` reports the "Publish to PyPI" step as failed

The build job (sdist + wheel + tests + lint) is independent from the
publish job (OIDC handshake + upload). The publish step occasionally
fails with no obvious cause — observed on v0.4.2 — even though the
artifact built clean and the trust is configured. Symptoms:

- Actions UI shows "Build sdist + wheel" ✅ and "Publish to PyPI" ❌
- PyPI does not list the new version
- No error message visible in the action summary

**Fix**: Actions UI → the failed run → top-right **Re-run failed
jobs**. Same artifact gets re-uploaded; OIDC re-issues. Usually
goes green on the retry.

If a second retry also fails, fetch the actual log
(`gh run view <run_id> --log-failed`) and check for one of:

- *"Forbidden"* on the upload → Trusted Publisher misconfigured;
  revisit §1.2.
- *"This filename has already been used"* → the wheel was actually
  uploaded on the original attempt and the failure was a network
  hiccup *after* the upload completed. Bump to the next patch.
- *"503"* / *"Bad Gateway"* → transient PyPI; wait 5 min and re-run.

### 2.5 Cut the GitHub Release page

The tag is enough for `pip install` and `docker pull`. For a polished
Release page (rendered changelog, downloadable assets):

- Web UI: https://github.com/SauNu84/ocsf-parse/releases/new?tag=vX.Y.Z
- Title: `vX.Y.Z — <short summary>`
- Description: paste the corresponding section from `CHANGELOG.md`.
- "Publish release".

Or via the API (`gh` CLI if installed, or the curl/Python POST from
the earlier tag — see `git log --grep="v0.2.0"` for the inline
script).

### 2.6 Post-release smoke test

```bash
# Fresh venv just to make sure the published artifacts are usable.
python3 -m venv /tmp/smoke
/tmp/smoke/bin/pip install 'ocsf-mapper[web,parquet,fast]==X.Y.Z'
/tmp/smoke/bin/ocsf-mapper --version
/tmp/smoke/bin/ocsf-mapper list

# Container.
docker pull ghcr.io/saunu84/ocsf-mapper:X.Y.Z
docker run --rm ghcr.io/saunu84/ocsf-mapper:X.Y.Z --help
```

If either fails after a green workflow run, something's off with
the package metadata — check `pyproject.toml` matches the tag, and
that the Dockerfile's `pip install -e '.[…]'` line includes any
new extras.

---

## 3. Versioning policy

Loosely [SemVer](https://semver.org/):

- **Patch** (`X.Y.Z` → `X.Y.Z+1`) — bug fixes, doc updates, new
  mappings. No API surface change.
- **Minor** (`X.Y` → `X.Y+1`) — new features, new CLI subcommands,
  new sinks/providers. Backwards compatible.
- **Major** (`X` → `X+1`) — anything that changes the JSON DSL in a
  non-additive way, removes an op kind, or renames a CLI subcommand.

The OCSF schema version is tracked separately (vendored as a git
submodule, see `ocsf-schema/version.json`). A schema bump is
**always** at least a minor version on our side, and the
`ocsf-mapper schema-diff` output should run in CI before tagging
to catch any silent breakage in pinned mappings.

---

## 4. If something goes wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| `publish.yml` fails with 403 | Trusted Publisher not configured, or env name mismatch | Re-check §1.2 |
| `publish.yml` fails with `version mismatch` | `pyproject.toml`, `__init__.py`, or tag disagree | The workflow's sanity-check step prints all three; bring them into agreement and re-tag (delete the old tag locally + remote, push the new one) |
| `docker.yml` fails with 401 / 403 | Token permissions; usually a fresh repo | `Settings → Actions → General → Workflow permissions` → "Read and write permissions" |
| `pages.yml` succeeds but page doesn't load | Pages source not set to "GitHub Actions" | Re-check §1.1 |
| `pip install ocsf-mapper` returns old version | PyPI cache; takes 1-2 min to propagate | Wait, then `pip install --no-cache-dir` |
