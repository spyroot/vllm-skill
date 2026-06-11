# Repository Sync Model

Use one source of truth for code. Recommended setup:

```text
GitHub main  ->  GitLab pull mirror  ->  GitLab CI with private variables
```

GitHub stores portable source code, examples, and documentation. GitLab stores private CI/CD variables, File
variables, protected runner settings, and manual live-smoke controls.

GitHub Actions may run portable offline validation and coverage. GitHub must not require GitLab-only team files,
sandbox profiles, kubeconfigs, token-bearing `.env` files, private URLs, or live cluster access.

## GitLab Pull Mirroring

GitLab pull mirroring is automatic, but scheduled. It is not guaranteed to update at the exact moment a GitHub
push lands. Maintainers can force a sync from the GitLab UI with **Update now**, subject to the instance mirror
interval limit.

Configure it in GitLab:

1. Open the GitLab project.
2. Go to **Settings > Repository**.
3. Expand **Mirroring repositories**.
4. Add the GitHub repo as a **Pull** mirror.
5. Use an authenticated URL or SSH mirror key if the GitHub repo is private.
6. Prefer mirroring only protected branches for production use.
7. Enable pipeline runs for mirrored updates if the GitLab instance exposes that option.

For SSH mirroring, use an `ssh://` URL form:

```text
ssh://git@github.com/spyroot/vllm-skill.git
```

If GitLab generates an SSH public key for the mirror, add that key to the GitHub repo as a read-only deploy key.

## Why Not GitHub Webhooks

GitHub webhooks require GitHub to reach the GitLab URL. For private lab or VPN GitLab instances, inbound webhook
delivery from GitHub is usually blocked. Pull mirroring lets GitLab initiate the outbound fetch instead.

## Why Not Bidirectional Mirroring

Bidirectional mirroring can create conflicts when both sides receive commits. Keep GitHub as the code source and
GitLab as the private CI execution target unless there is a strong reason to reverse the flow.

## CI Secrets Stay In GitLab

Do not mirror secrets into GitHub. Keep these in GitLab CI/CD variables:

- `HF_TOKEN` or `HF_TOKEN_FILE`
- `KUBECONFIG_FILE` or `KUBECONFIG_B64`
- `DAEDALUS_RUNTIME_PROFILE_FILE`, `DAEDALUS_RUNTIME_PROFILE_B64`, or `DAEDALUS_RUNTIME_PROFILE`
- `LIVE_OPENSHIFT_SMOKE`

GitHub contains portable examples. GitLab contains the private CI/runtime settings.

## Manual Fallback

If pull mirroring is unavailable on the GitLab instance, push both remotes from a trusted machine:

```bash
git remote add gitlab <ssh-or-https-gitlab-url>
git remote -v
```

The `gitlab` remote above is a local Git remote name created on your workstation; it should point at the private
GitLab project that holds CI/CD variables and protected live-smoke settings.

```bash
git push origin main
git push gitlab main
```

The GitLab SSH host key must be trusted locally before `git push gitlab main` works.
