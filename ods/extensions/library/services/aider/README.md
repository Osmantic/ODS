# Aider

Aider edits a mounted Git project from the terminal using the currently selected
ODS model through `http://litellm:4000/v1` and `openai/ods/current`. It does not
select a different model or change the context window. Model coding quality
still depends on the selected model. No dedicated GPU is required by this client.

## Install and use

Enable Aider from Extensions or `/extensions @aider`. The recipe declares the
ODS LiteLLM dependency. Installation runs the upstream `aider --version` command,
which checks that the executable starts without sending a model request. There
is no web page or permanent background service for this CLI.

Place the project in the ODS installation's `data/aider` directory. From the ODS
installation directory, invoke the installed recipe explicitly:

```bash
docker compose --project-directory . --env-file .env -f data/user-extensions/aider/compose.yaml run --rm aider --model openai/ods/current src/main.py
```

`--model` and the file arguments replace the installation's default `--version`
command. Paths are relative to `/app`, which mounts `data/aider`. Verify the
installed Compose path in your ODS layout; it may differ after a custom install.
Configure Git identity inside that project before asking Aider to commit changes.
Do not mount your entire home directory or SSH credentials merely to start it.

On Linux, the upstream container runs as UID 1000; the mounted project must be
writable by that identity. For another owner, supply Docker's `--user UID:GID`
option to the `run` command. Windows/macOS use Docker Desktop's file sharing.
The command itself is portable; shell-specific UID substitutions are not needed.

## Model connection and data

The container receives the existing ODS `LITELLM_KEY` as its OpenAI-compatible
credential. The recipe does not silently choose OpenRouter, Claude or another
paid provider. To use a different provider, configure it in ODS; explicit Aider
command-line overrides remain available to experienced users. Secrets should
not be passed in chat or committed into the project.

Project files, Git history and Aider's local state persist in `data/aider`.
Disabling/removing the recipe does not delete that directory. Aider can modify
and commit project files during an interactive session. Its `/run` commands
execute inside the container, so project-specific test dependencies may need a
separate development image. Voice/GUI support is not implied by this CLI recipe.

## Upstream references

- [Official Docker usage](https://aider.chat/docs/install/docker.html)
- [Pinned-version Dockerfile](https://github.com/Aider-AI/aider/blob/v0.86.2/docker/Dockerfile)

Only configuration and mocked lifecycle validation were run for this update;
application installation and model execution are left to the owner.
