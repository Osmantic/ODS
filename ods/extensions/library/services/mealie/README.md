# Mealie recipes and meal planning

Keep recipes, ingredients, instructions and scheduled household meals locally.
An authenticated API and revocable account tokens allow an ODS workflow to save
and retrieve recipes. No hosted AI provider is required for manual recipes or
meal planning, and this recipe does not configure one.

## Install and take ownership locally

1. Set `BIND_ADDRESS=127.0.0.1` in the ODS `.env` **before** enabling Mealie. Copy
   this directory into `extensions/services/mealie`, then run `ods enable mealie`
   and `ods up`. Open `http://localhost:7835` on that host.
2. The pinned upstream image creates `changeme@example.com` with password
   `MyPassword` on an empty database. Immediately sign in, change the password
   to a strong unique value and replace the account's email with your own in
   account settings. Verify a fresh login and rejection of the initial password.
3. Keep the listener on loopback until this is complete. Only then configure a
   protected remote-access path and its `MEALIE_BASE_URL`. Changing `MEALIE_PORT`
   also requires updating that URL. A healthy service does not prove ownership
   setup is complete. Public signup is disabled; additional accounts remain
   subject to Mealie's administrator/invitation controls.

The initial credentials are upstream bootstrap behavior, not configurable
`DEFAULT_PASSWORD`/`DEFAULT_EMAIL` environment fields in this version. Existing
database credentials remain authoritative after container replacement. This
recipe does not claim unattended account provisioning.

Create a recipe, enter its ingredients and steps, and add it to the meal planner.
Recipes default to private. Group/household membership and any deliberate public
sharing remain governed by Mealie. Create a separate API token in your profile
for automation, store it privately, and revoke it when the workflow is removed.
Do not place passwords or tokens in recipe content.

## Runtime and data

Official `v3.26.0` is pinned by digest. UID/GID 1000 matches ODS directory
preparation and the upstream entrypoint's non-root path. The container has a
read-only root, bounded temporary storage and no extra capabilities. SQLite,
recipe assets and backups live in `data/mealie`; no PostgreSQL server is needed.

Stop the service and back up the whole data directory before changing versions.
After migrations, rollback may require restoring the matching backup as well
as the previous image. Do not synchronize a live SQLite directory. Disabling
the extension preserves its stored data.

URL scraping, imported remote images, email, webhooks, OIDC and AI providers can
contact other services if an operator configures or invokes them. They are not
part of the local manual-recipe contract tested here. Local HTTP tests cover
password rotation, private Unicode recipes, meal scheduling, token persistence
and revocation after recreation. Browser rendering, external recipe imports,
AI generation, native ARM/macOS/Windows, large libraries and existing-database
upgrades need separate validation.

Upstream configuration: <https://docs.mealie.io/documentation/getting-started/installation/backend-config/>.
Pinned source: <https://github.com/mealie-recipes/mealie/tree/v3.26.0>.
