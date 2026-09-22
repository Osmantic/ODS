# LanguageTool API

Local spelling and grammar checks using the open-source rules and dictionaries, including Portuguese variants. The native `/v2/check` API returns rule matches, offsets and suggested replacements. It does not rewrite the chat automatically or substitute canned responses for the selected model.

## Distribution

Builds the upstream standalone distribution from the SHA256-verified `v6.6` source archive with Maven and Java 21. Version 6.6 is the stable ZIP listed on the official download index at research time, not a claim about the newest nightly snapshot. The public binary download returned HTTP 403, so this recipe builds the equivalent source distribution instead. Maven dependencies are resolved at build time; their availability and the complete build remain unverified.

The application is LGPL-2.1-or-later; bundled dependency notices are preserved in the assembly. The repository tag and checksum in `upstream.json` identify the corresponding source. Cloud AI/premium rules, synonyms, fastText and optional n-gram datasets are not included or silently downloaded.

## Using it in a project

This is an API extension, with no fake application homepage. On the host use `http://localhost:11086`; ODS container clients use `http://languagetool:8081`. A client can list supported variants at `/v2/languages` and submit URL-encoded text:

```sh
curl --data-urlencode 'language=pt-BR' --data-urlencode 'text=O texto para revisar.' http://localhost:11086/v2/check
```

Send text through POST rather than embedding document contents in URLs. Use the actual document language when known; automatic detection is less accurate without optional language-identification resources. Review suggestions before applying them to project files. Installing the service does not create a Portal client or authorize automatic edits.

## Operation

Only loopback port `${LANGUAGETOOL_PORT:-11086}` is published. `--public` enables binding inside Docker so ODS clients can reach it; it does not publish a public host port. No authentication is configured, and other containers on `ods-network` can call it. Cross-origin browser access is not enabled; a client requiring CORS needs an explicit trusted-origin integration.

The service is stateless with a read-only filesystem and temporary directory. It has no fake document volume, and submitted text is not stored as project history. Check logging and result caching are disabled. Requests are limited to 20,000 characters, 20 seconds and two checking threads with a bounded queue. Heap is limited to 1536 MiB within a 2 GiB container limit; no GPU or chat-model change is required.

The Java runtime has Linux amd64/arm64 variants for Docker on Windows, Linux and macOS. Build, actual language checks and platform behavior remain pending. `/v2/languages` only establishes API availability, not correctness of every language rule.

Sources: https://dev.languagetool.org/http-server and https://github.com/languagetool-org/languagetool/tree/v6.6
