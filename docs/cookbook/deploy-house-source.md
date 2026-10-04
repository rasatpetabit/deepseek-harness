# Select an immutable house deployment source

English | [中文](deploy-house-source.zh.md)

## Summary

Select one committed worktree for the house deployment script without cleaning, stashing, or building another checkout. This procedure prepares source-selection evidence; it authorizes no live deployment or restart. The [reconciliation decision](../../.agents/notes/proposed/architecture/2026-10-04-upstream-reconciliation.md) owns release review and activation prerequisites.

## Table of Contents

- [1. Select the reviewed source](#1-select-the-reviewed-source)
- [2. Verify scratch deployment behavior](#2-verify-scratch-deployment-behavior)
- [3. Hand off for activation](#3-hand-off-for-activation)

## 1. Select the reviewed source

Provide both `--source <absolute-canonical-worktree>` and `--expected-sha <full-commit-SHA>` to [deploy-dsh.sh](../../scripts/deploy-dsh.sh). The source must be a registered worktree of the owning repository at `/srv/dev/ai/deepseek-harness`; another clone, a symlink alias, a relative path, or a subdirectory is not accepted. The expected SHA must be the exact reviewed commit and match the source HEAD. There is no default source or fallback to main. Add `--prepare-only` to retain a confined release with `RELEASE_COMMIT` without promotion, restart or pruning; use the [commissioning procedure](deploy-house-commissioning.md) for exact-release review and activation.

Keep the selected worktree exclusive to the deployment until it finishes. Its index, tracked files, and non-ignored untracked files must be clean before deployment and remain clean after dependency installation, build, and output staging. Ignored generated outputs and worktree-local dependencies are permitted; shared dependency links outside the release fail final confinement. Do not commit or stash operator-owned changes in another checkout to satisfy this requirement.

## 2. Verify scratch deployment behavior

From the candidate worktree with Python, pytest, Git, Bash, rsync, and standard Linux utilities available, run:

```sh
python3 -m pytest -q scripts/tests/test_deploy_source.py scripts/tests/test_deploy_overlay.py scripts/tests/test_deploy_provisioning.py scripts/tests/test_deploy_retention.py
bash -n scripts/deploy-dsh.sh
```

The source tests run the real script with only its development/production roots replaced by scratch paths and controlled build/service commands. They exercise real Git archives, output copying, release confinement, and promotion without touching a running service. Require passing tests and shell syntax validation; these checks do not establish compatibility of a built production profile.

## 3. Hand off for activation

The release operator supplies the reviewed canonical source path and full commit SHA after independent exact-artifact review and all commissioning prerequisites. Deployment archives that fixed SHA and overlays only the selected worktree's outputs. Rejection leaves the active release unchanged and does not restart the service; staging failures remove the unpromoted release. Existing unit provisioning, deployment locking, service-health verification, and bounded release retention remain in force.

Keep consistent rollback data, plugins, settings, credentials, service configuration, and edge configuration outside `/srv/dev`. The script does not modify the production data directory or rotate credentials. A failed health check after promotion still requires the existing operator rollback procedure; source validation does not provide automatic rollback or durable-format downgrade support.
