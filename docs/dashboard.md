# Standalone dashboard

The dashboard is a separate read-only process, not part of the bot or Error API.
It uses existing FastAPI/uvicorn dependencies and listens on `0.0.0.0:8080` by
default. It does not configure Tailscale or trigger deployment.

## CI/CD deployment (normal path)

Use the existing **Deploy Dev** or **Deploy Prod** GitHub Actions workflow from a
ref containing the dashboard changes. No new GitHub secrets, runner `.env` edits,
manual SQL, or manual Compose commands are required for an existing configured
installation. Agents may only trigger dev deployments when explicitly requested;
production remains user-triggered.

The workflows automatically:

1. Generate a random, dedicated `db_dashboard` password on the runner, once.
   Store its role/password/URL in `<deploy-dir>/.dashboard.env` with mode `0600`.
   Keep the existing `.env` unchanged and reuse the generated file on redeploy.
2. Validate the generated URL, role separation, and password separation in preflight.
3. Fetch `compose.dashboard.yaml` and use it before the dev/prod image overlay in
   every Compose command, enabling the `dashboard` profile.
4. Pull the same built image as the bot. After both existing migration histories,
   run `dashboard-bootstrap` to create/validate the reader and apply column-only
   SELECT grants. Owner credentials exist only in the one-shot bootstrap, never
   in the dashboard runtime.
5. Start the dashboard alongside the bot and Error API and wait for its HTTP/data
   health check. The bot and Error API do not depend on dashboard availability.

Default URLs (distinct ports allow both environments on the same Pi):

- Dev: `http://<host>:8081`
- Prod: `http://<host>:8080`

`DASHBOARD_PORT` and `DASHBOARD_BIND_HOST` can optionally override the published
port and address in the existing runner `.env`. Defaults listen on `0.0.0.0`.
There is **no built-in authentication**: restrict access to trusted clients with
your firewall; do not publish this service to the internet. Tailscale is unchanged.

Back up `.dashboard.env` with the deployment configuration. Do not delete it to
rotate credentials: an existing role with a mismatched password fails closed.
Restore the matching credential file with the database. A changed database name
also requires updating the stored URL. No credentials are printed or committed.

## Local development

`uv run poe dashboard` starts the standalone process on `0.0.0.0:8080`.
Set `DASHBOARD_DATABASE_URL` to a provisioned reader's `postgresql+psycopg://` URL
for real data. `DASHBOARD_HOST` and `DASHBOARD_PORT` override its listener.
Without a URL the UI explicitly shows “Database not configured”, never demo data.
For local Compose setup, the same preparation helper and dashboard overlay can
be used; CI/CD already performs these steps automatically.

The dashboard queries only selected columns from `public.media_requests`,
`observability.error_groups`, and `observability.error_occurrences`. No migrations
or changes to existing runtime roles are needed. There are no write endpoints.
Each snapshot uses a read-only, repeatable-read transaction and a five-second
per-query timeout. Usernames, request URLs, tracebacks, and occurrence messages
are not read. Error group names/types can still contain sensitive information.

## Tabs and metric definitions

- **General usage:** detected requests, distinct known users/chats, confirmed
  delivery rate, daily request activity, and provider counts.
- **Successes & errors:** confirmed delivery, recorded download failures,
  fetched-but-unconfirmed delivery, incomplete requests, daily outcomes, and
  download failure reasons. Unconfirmed delivery includes in-flight requests:
  the schema cannot distinguish these from failed sends. Failure reasons include
  intentional unsupported outcomes. Historical null user/chat IDs are not counted
  as active users/chats.
- **Error ledger:** groups with occurrences in the selected window, searchable
  by ID/name/event and filterable by exception type/current status. Counts reflect
  occurrences within the window, not lifetime totals. At most 500 recently seen
  groups are loaded, with an explicit truncation notice. No status mutations.

Time windows are rolling 1–90 days, using UTC request creation times or ledger
occurrence times. Daily charts show days with requests; absent days have zero
requests. Request metrics and ledger errors are not interchangeable: one request
can produce multiple occurrences, and some errors are unrelated to requests.
Refresh is manual or triggered by a time-window change.

## Validation

`uv run pytest tests/unit/test_dashboard.py -v` checks the HTTP contract, query
bounds, and unavailable-data behavior. To smoke-test a provisioned read-only
connection without modifying any rows:

```bash
DASHBOARD_TEST_DATABASE_URL='<reader URL>' uv run pytest tests/integration/test_dashboard_database.py -v
```
