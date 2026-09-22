# Presidio for ODS

This extension runs the official Presidio analyzer and anonymizer as one installable unit. It provides local entity detection and text redaction for document pipelines. It does not replace or automatically intercept the Portal chat. ODS's existing Privacy Shield currently uses regex detection; this is the separate NLP analysis service, not another copy of that proxy.

## Use the APIs

Enable Presidio in Extensions. The analyzer listens at `http://localhost:11020`, and the anonymizer at `http://localhost:11021`. Applications on the ODS Docker network use `http://presidio:3000` and `http://presidio-anonymizer:3000`. Host ports are configurable with `PRESIDIO_PORT` and `PRESIDIO_ANONYMIZER_PORT`.

POST JSON to `/analyze` on the analyzer:

```json
{"text":"Contact me at person@example.com","language":"en","entities":["EMAIL_ADDRESS"]}
```

Pass the returned array as `analyzer_results`, together with exactly the same original `text`, to `/anonymize` on the anonymizer. For replacement, add `anonymizers` with `DEFAULT` set to `{"type":"replace","new_value":"[REDACTED]"}`. Offsets belong to the original string; changing the text between calls invalidates them. The included `redact.py` performs both requests for a UTF-8 text file using Python's standard library:

```text
python redact.py input.txt
```

The command prints only the anonymized result, not the original text or recognition response. It processes the file only when you explicitly run it. Use `--help` to change endpoints and language.

## Language and limits

The default analyzer image bundles an English spaCy pipeline. Query `/supportedlanguages` and `/supportedentities` before using another language. Portuguese requires a matching installed NLP model and recognizer configuration; changing the `language` request alone is insufficient. Detection can miss personal information or classify ordinary text incorrectly. Review outputs for the intended dataset; do not treat a successful health check as a privacy guarantee.

No LLM, GPU or Ollama server is required by this recipe. Both official images support Linux amd64 and arm64, usable through Linux-container Docker on Windows, Linux and macOS. The analyzer is capped at 3 GB RAM and one worker; the anonymizer at 512 MB. Initial image download includes NLP weights.

## Persistence and lifecycle

These are stateless APIs. They have no input database or document volume. Keep source and result files in your project; the recipe does not automatically retain them. Docker logs are rotated. Disable stops both namespaced containers with the updated ODS host agent. Endpoints are loopback-bound by default and have no built-in API authentication; intentional remote exposure requires an authenticated reverse proxy.

Registry/schema/staging validation is separate from runtime validation, which remains pending. Verify health for both APIs, supported language, a known synthetic detection/redaction example and behavior on representative documents before relying on results.

Upstream: [Microsoft Presidio](https://github.com/microsoft/presidio), MIT. Image digests and architecture evidence are in `upstream.json`.
