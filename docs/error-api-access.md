# Error API Access

This guide explains how to issue, use, rotate, and revoke credentials for the private Error API.

## Authentication model

The Error API uses static bearer keys from the deployment environment. It has no users table, credential table, login endpoint, or token-issuance endpoint.

Two scopes are available:

| Scope | Access |
| --- | --- |
| `read` | Health checks, error lists and details, occurrences, reproduction cases, and notes |
| `triage` | All read operations plus rename, status, note, link, and mark-fixed operations |

Each scope has one required current key and one optional next key for rotation:

```text
ERROR_API_READ_KEY
ERROR_API_READ_LABEL
ERROR_API_READ_KEY_NEXT
ERROR_API_READ_LABEL_NEXT

ERROR_API_TRIAGE_KEY
ERROR_API_TRIAGE_LABEL
ERROR_API_TRIAGE_KEY_NEXT
ERROR_API_TRIAGE_LABEL_NEXT
```

A key and its label must always be configured together. The label is a non-secret audit identity; note creation stores the authenticated triage label as the note actor.

The current design supports at most two simultaneously valid keys per scope during rotation. It does not issue separate credentials to an arbitrary number of users or agents.

## Issue initial credentials

1. Generate independent keys for the read and triage scopes. Run this command once for each key:

   ```bash
   python -c 'import secrets; print(secrets.token_urlsafe(48))'
   ```

2. Add the keys and descriptive, non-secret labels to the deployment host's untracked `.env` file:

   ```text
   ERROR_API_READ_KEY='<generated-read-key>'
   ERROR_API_READ_LABEL='agent-read'
   ERROR_API_READ_KEY_NEXT=''
   ERROR_API_READ_LABEL_NEXT=''

   ERROR_API_TRIAGE_KEY='<different-generated-triage-key>'
   ERROR_API_TRIAGE_LABEL='agent-triage'
   ERROR_API_TRIAGE_KEY_NEXT=''
   ERROR_API_TRIAGE_LABEL_NEXT=''
   ```

3. Recreate the `error-api` service so Docker loads the changed environment. A plain `docker compose restart` does not reload `.env` values.

   From the deployment directory, use the same Compose files and project name as the deployment. For example:

   ```bash
   docker compose \
     --env-file .env \
     -p ig-reel-downloader-prod \
     -f docker/compose.yaml \
     -f docker/compose.prod.yaml \
     up -d --no-deps --force-recreate error-api
   ```

4. Give each client only the API URL and the least-privileged key it needs. Never provide database credentials or the deployment `.env` file.

## Publish the API to the tailnet

Compose binds the Error API only to the deployment host's loopback interface.
For dev, the default listener is `http://127.0.0.1:8001`; use the configured
`ERROR_API_HOST_PORT` instead if it was overridden.

Run these commands on the deployment host:

1. Inspect existing Serve configuration before changing it:

   ```bash
   tailscale serve status
   ```

   If HTTPS port 443 already serves another application, do not overwrite that
   configuration. Choose an unused HTTPS port or intentionally add a separate
   path according to the host's existing Serve layout.

2. Publish the dev listener as persistent, tailnet-only HTTPS:

   ```bash
   sudo tailscale serve --bg --https=443 http://127.0.0.1:8001
   ```

3. Read the generated tailnet URL:

   ```bash
   tailscale serve status
   ```

   The output includes a URL such as:

   ```text
   https://dev-pi.example-tailnet.ts.net
   ```

   Use that complete origin as `ERROR_API_URL`. If Serve uses a non-default
   HTTPS port, retain the port in the URL.

4. Restrict the hostname to the required operator and agent identities with
   tailnet ACLs or grants. The bearer key remains mandatory as a second access
   boundary.

Use `tailscale serve`, never `tailscale funnel`. Funnel would make the service
publicly reachable. Do not change Compose to bind the Error API to a public
host interface. The `--bg` flag makes the Serve configuration survive host and
Tailscale restarts.

Tailscale Serve is host configuration, not part of the Compose deployment.
Configure it once per host and recheck it when the local port or tailnet
hostname changes.

## Configure a client

Set the private HTTPS endpoint and one scoped key on the operator or agent machine:

```bash
export ERROR_API_URL='https://errors.example-tailnet.ts.net'
export ERROR_API_KEY='<scoped-key>'
```

Verify read access:

```bash
uv run bot-ops errors list --status new
uv run bot-ops errors show ERR-1842
```

A read key receives HTTP `403` for triage commands. Use a triage key only where mutation access is required:

```bash
uv run bot-ops errors status ERR-1842 investigating
uv run bot-ops errors note ERR-1842 'Reproduced in dev'
```

## Rotate a key without downtime

Rotate one scope at a time:

1. Generate a new key.
2. Put it in that scope's `*_KEY_NEXT` variable and set the matching `*_LABEL_NEXT`.
3. Recreate only the `error-api` service. Both current and next keys are now valid.
4. Move every client for that scope to the next key and verify access.
5. Copy the next key and label into the current variables, clear both next variables, and recreate `error-api` again.
6. Delete the retired key from client configuration and secret storage.

Rotation does not require a database migration or bot restart.

## Revoke a compromised key

There is no database revocation list. Replace the compromised environment key with a new key and recreate `error-api`. The old key stops working when the recreated service becomes active.

If uninterrupted access matters, first activate a next key, move trusted clients, then replace the compromised current key.

## Validation rules

Deployment preflight and runtime startup reject invalid credential configuration:

- Keys must be 32-4096 ASCII characters.
- Keys may contain letters, digits, `-`, `.`, `_`, `~`, `+`, `/`, and trailing `=` padding.
- Every configured key must be distinct.
- Labels must start with a letter or digit, contain only letters, digits, `_`, `.`, or `-`, and be at most 128 characters.
- A next key without its next label, or a next label without its key, is invalid.

Treat keys as secrets. Do not commit them, print them in logs, place them in command history, or store them in the repository.
