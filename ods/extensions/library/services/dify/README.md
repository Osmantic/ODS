# Dify — reference entry, not installable

This entry has no deployable ODS recipe. The catalog and installation API
already reject entries without `compose.yaml`; neither `ods enable dify` nor
renaming a disabled template is a supported Dify installation.

The obsolete template referenced `difyai/dify:0.6.16`, which is not the upstream
Dify deployment image. It also assumed a single process and SQLite, whereas
the upstream deployment requires separate API/web/worker services, a database
and other dependencies. That misleading template has been removed. This change
does not remove or migrate any installed container, database or user data.

A future ODS integration must package and validate the complete upstream stack,
including credentials, network boundaries, persistent storage and migrations;
substituting only an image name would not produce a working installation.

For a separate deployment, follow the
[official Dify Docker Compose installation documentation](https://docs.dify.ai/en/self-host/quick-start/docker-compose).
The [historical 0.6.16 Compose definition](https://github.com/langgenius/dify/blob/0.6.16/docker/docker-compose.yaml)
documents why the removed single-container template was not deployable.
