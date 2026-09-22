# LanguageTool

Use local spelling and grammar checks in a writing client or an ODS automation.
This is the open-source LanguageTool 6.8 HTTP API, packaged by the community
`meyay/languagetool` image (6.8-10), which the LanguageTool project lists among its
Docker options. The exact multi-architecture image is pinned by digest. It is not
the hosted premium service and has no browser editor or account system.

## Start and check text

Install LanguageTool from the Extensions library. The default endpoint on the
ODS host is `http://127.0.0.1:7826/v2`; `LANGUAGETOOL_PORT` changes the published
port. The Extensions page treats it as an API service rather than offering a
browser-editor link.

```bash
# List supported language codes
curl --fail --show-error http://127.0.0.1:7826/v2/languages

# Check a sentence without sending it to the hosted LanguageTool service
curl --fail --show-error http://127.0.0.1:7826/v2/check \
  --data-urlencode 'language=en-US' \
  --data-urlencode 'text=This is a sentnce.'
```

The response includes rule matches, text offsets and suggested replacements.
Use `http://languagetool:8081/v2` from another container on `ods-network`.
An editor must support a custom LanguageTool server URL; point it at the address
reachable from that editor's machine. Some clients require HTTPS, in which case
configure a protected TLS reverse proxy. No specific browser add-on or editor
integration is enabled by installing this recipe.

## Limits and local operation

The service uses bundled language rules/dictionaries. FastText and automatic
n-gram downloads are disabled, so it can start and check explicit languages
without fetching additional models. Optional statistical models, premium rules
and hosted AI rewriting are not included. Language support and rule coverage
vary; obtain supported codes from `/v2/languages` and specify a language for
predictable results.

Requests are limited to 20,000 characters, with two checking threads, a 1.5 GiB
Java heap and a 2 GiB container limit. Split longer documents into suitable chunks.
The service runs as UID/GID 783 with a read-only filesystem; a temporary executable
`/tmp` is required by the image's Java/native-library startup. It has no persistent
database, custom word-list volume or result history in this recipe.

The API has no authentication and binds to loopback by default. Only allow
trusted clients to access it; add access control and TLS before exposing it
outside the host. The image runs locally, but clients must actually use this
endpoint to avoid sending their text to a hosted service. Disabling the extension
stops checks; re-enabling starts a fresh process with the same bundled rules.

## Verification and rollback

The opt-in boundary test runs the exact installed recipe on an isolated Docker
network without external connectivity. It checks language discovery, a real
spelling suggestion, grammar correction, request limits and a repeated check
after recreation. This does not certify every supported language or integration.
Native ARM/macOS/Windows activation and editor/browser clients require separate
verification. Rollback restores the prior image pin or removes the optional
extension; there is no database migration.

Upstream: [LanguageTool and its Docker options](https://github.com/languagetool-org/languagetool),
[HTTP server](https://dev.languagetool.org/http-server),
[image source and configuration](https://github.com/meyayl/docker-languagetool/tree/6.8-10).
