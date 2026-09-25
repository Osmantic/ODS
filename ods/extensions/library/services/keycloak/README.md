# Keycloak

Identity service for application login through OpenID Connect and SAML. Realms, clients, users and federation are managed in Keycloak; installation does not replace ODS authentication.

## Bootstrap and projects

Provide distinct random 64-hex KEYCLOAK_DB_PASSWORD and KEYCLOAK_BOOTSTRAP_PASSWORD values. Open http://localhost:11141/admin/ and use ods-bootstrap with the bootstrap password. This native temporary administrator is created only when the master realm does not exist. Create and verify permanent administrative access, then remove the temporary account. Changing bootstrap variables on an existing database does not reset accounts; follow native recovery procedures if access is lost. PostgreSQL password rotation also requires updating the existing database credential, not just its environment.

Create a dedicated realm and register the actual OIDC/SAML application. Set exact callback/logout URIs, allowed origins and the correct client type. Public browser clients should use their supported authorization-code/PKCE flow; confidential backends keep secrets server-side. No wildcard client, realm import, LDAP federation, test user or external account is created by this recipe. Project association remains an explicit setup step.

The canonical issuer is http://localhost:11141, adjusted by KEYCLOAK_PORT. Browser and backend must agree on the issuer and reach it. A backend container cannot reach the host using its own localhost. Configure a shared reachable hostname and corresponding issuer/ingress for that topology: changing only the backend URL to keycloak:8080 can break issuer validation. ODS does not rewrite tokens or application configuration.

## Deployment and persistence

HTTP is explicitly enabled for loopback-published local use. The optimized server uses private PostgreSQL and local cache for a single instance. Remote deployment requires a real canonical HTTPS hostname and appropriate TLS/proxy handling. Management/metrics port 9000 has no host publication. Native health is deliberately served on the main local HTTP port so ODS can check readiness.

keycloak-db-data retains realms, users, signing keys, credentials and clients. keycloak-data retains native runtime data under /opt/keycloak/data. Back up a consistent stopped stack and its configuration, or use a supported online PostgreSQL backup. Realm export alone is not a complete database backup. Providers/themes are image-managed; no third-party JARs are downloaded.

UID1000 runs with a read-only root, temporary /tmp and writable data volume. Image build performs native PostgreSQL/health/metrics optimization; startup uses start --optimized with signals preserved. Limits are two CPUs/2 GiB for Keycloak and 1 GiB for PostgreSQL. GPU/model selection is unchanged.

The /health/ready check includes database-pool readiness because metrics are enabled. Its probe uses the bash TCP support present in the official minimal image. Readiness does not verify application login/logout or token validation.

## Provenance and verification

Apache-2.0 Keycloak 26.7.4, official digest-pinned amd64/arm64 image, with pinned PostgreSQL 17. Linux-container runtimes on Windows, Linux and macOS use named volumes; issuer/DNS/proxy configuration remains topology-specific.

Build/optimization, bootstrap/permanent login, OIDC/SAML exchange and restore/platform execution remain unverified. No containers, realms, accounts, browsers or models were started.

Sources: [containers](https://www.keycloak.org/server/containers), [bootstrap semantics](https://www.keycloak.org/server/bootstrap-admin-recovery), [readiness](https://www.keycloak.org/observability/health).
