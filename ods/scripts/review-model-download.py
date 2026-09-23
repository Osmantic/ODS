#!/usr/bin/env python3
"""Review an exact catalog GGUF before an installer starts downloading it.

Python 3.8+, standard library only. This helper never downloads model files or
accepts publisher terms. Its receipt records an operator's explicit review.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "extensions/services/dashboard-api"))
from model_terms import download_review_error, project_terms  # noqa: E402


def read_json(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


def artifact_identity(model):
    return {"file": model["gguf_file"], "url": model["gguf_url"], "sha256": model["gguf_sha256"]}


def resolve_model(catalog_path, filename, url, sha256, model_id=None):
    if (not filename or Path(filename).name != filename or "/" in filename or "\\" in filename
            or not url or not re.fullmatch(r"[a-f0-9]{64}", sha256 or "")):
        raise ValueError("A filename, exact catalog URL and SHA-256 are required; no unpinned fallback is allowed.")
    catalog = read_json(catalog_path)
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list):
        raise ValueError("The model catalog is missing its model records.")
    matches = [model for model in models if isinstance(model, dict)
               and (model_id is None or model.get("id") == model_id)
               and model.get("gguf_file") == filename and model.get("gguf_url") == url
               and model.get("gguf_sha256") == sha256]
    if len(matches) != 1:
        raise ValueError("The selected file, URL and SHA-256 do not identify exactly one catalog model. Refresh the installer selection; use the dashboard model library for supported downloads.")
    model = matches[0]
    if not isinstance(model.get("id"), str) or not model["id"]:
        raise ValueError("The catalog model identity is missing.")
    if model.get("gguf_parts"):
        raise ValueError("Split GGUF downloads require the dashboard model library; this installer downloads one file.")
    projection = project_terms(model)
    if not projection["recordValid"]:
        raise ValueError("Source and license information is incomplete: " + "; ".join(projection["errors"]))
    if any(not item.get("observed_present") for item in projection["terms"]["artifacts"]):
        raise ValueError("The catalog artifact was not observed at its recorded revision. Repair its identity before downloading.")
    return model, projection


def read_receipts(path):
    body = read_json(path)
    records = body.get("acknowledgements") if isinstance(body, dict) and body.get("schema_version") == 1 else None
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise ValueError("The acknowledgement file must use schema_version 1 and an acknowledgements list.")
    ids = [record.get("modelId") for record in records]
    if any(not isinstance(identity, str) or not identity for identity in ids) or len(ids) != len(set(ids)):
        raise ValueError("Acknowledgement records need unique modelId values.")
    return records


def receipt_acknowledgement(path, model):
    matches = [record for record in read_receipts(path) if record["modelId"] == model["id"]]
    if len(matches) != 1 or matches[0].get("artifact") != artifact_identity(model):
        raise ValueError("No acknowledgement is bound to this exact model file, URL and SHA-256.")
    return matches[0].get("termsAcknowledgement")


def display(projection, artifact, stream):
    terms = projection["terms"]
    def line(value=""):
        # Publisher metadata is text, never terminal control sequences.
        value = str(value)
        print("".join(char if char.isprintable() else " " for char in value), file=stream)
    line("Review model download: " + projection["modelId"])
    line("File: " + artifact["file"])
    line("URL: " + artifact["url"])
    line("SHA-256: " + artifact["sha256"])
    line("Terms digest: " + projection["termsDigest"])
    line("Terms reviewed" if projection["releaseReady"] else "License review pending")
    line("Commercial use: " + terms["commercial_use"])
    acceptance = {
        "required": "Read and accept the applicable terms under the recorded conditions.",
        "required_by_observed_gating": "Complete the acceptance or access step required by the publisher.",
        "not_required": "No separate acceptance step required by the reviewed terms.",
        "not_assessed": "Not yet assessed.",
    }
    line("Acceptance requirement: " + acceptance[terms["upstream_acceptance"]])
    line("Acknowledgement records review only. It does not grant rights, complete legal review, or accept terms with the publisher.")
    for source in terms["sources"]:
        line("Source ({role}): {repository} @ {revision}".format(**source))
        line("  " + source["url"])
        line("  Declaration: " + source["declaration_url"])
        line("  Declared license: " + str(source.get("license_id") or "not declared"))
        if source.get("license_url"):
            line("  License link: " + str(source["license_url"]))
    for base in terms.get("declared_base_repositories", []):
        line("Declared base (not reviewed): " + str(base))
    for condition in terms.get("conditions", []):
        line("Condition [{}]: {}".format(condition.get("trigger", ""), condition.get("requirement", "")))
    for document in terms["license_documents"]:
        line("License document: " + str(document.get("url", "")))
    for document in terms["notice_documents"]:
        line("Attribution/notice: " + str(document.get("url", "")))
    for document in terms.get("local_notices", []):
        line("Retained notice: {} ({})".format(document.get("path", ""), document.get("source_url", "")))
    for issue in terms["issues"]:
        line("Unresolved issue: " + issue)
    if terms.get("note"):
        line(terms["note"])
    line("Retain applicable license and attribution notices when redistributing model files.")


def interactive_acknowledgement(projection, input_stream, output_stream):
    if not input_stream.isatty():
        raise ValueError("Interactive terms review requires a terminal. For unattended downloads supply an explicit digest-bound --ack-file; --yes and EOF do not authorize a download.")
    digest = projection["termsDigest"]
    print("Download {} after reviewing these sources, terms and unresolved issues? [y/N] ".format(projection["modelId"]),
          file=output_stream, end="", flush=True)
    if input_stream.readline().strip().casefold() not in {"y", "yes"}:
        raise ValueError("Model download cancelled; no terms acknowledgement was provided.")
    acknowledgement = {"termsDigest": digest, "acknowledged": True}
    if projection["terms"]["upstream_acceptance"] in {"required", "required_by_observed_gating"}:
        question = ("Have you completed the acceptance or access step required by the publisher for {}? [y/N] "
                    if projection["terms"]["upstream_acceptance"] == "required_by_observed_gating"
                    else "Have you read and do you accept the applicable terms under the recorded conditions for {}? [y/N] ")
        print(question.format(projection["modelId"]),
              file=output_stream, end="", flush=True)
        if input_stream.readline().strip().casefold() not in {"y", "yes"}:
            raise ValueError("Model download cancelled; the recorded acceptance requirement was not explicitly confirmed.")
        acknowledgement["upstreamAccepted"] = True
    return acknowledgement


def write_receipt(path, model, acknowledgement):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refusing to overwrite a symlinked acknowledgement file.")
    records = read_receipts(path) if path.exists() else []
    records = [record for record in records if record["modelId"] != model["id"]]
    records.append({"modelId": model["id"], "artifact": artifact_identity(model),
                    "termsAcknowledgement": acknowledgement})
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, "acknowledgements": records}, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "config/model-library.json")
    parser.add_argument("--model-id")
    parser.add_argument("--file", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--ack-file", type=Path)
    parser.add_argument("--write-ack-file", type=Path)
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--show-json", action="store_true", help="Print observations without authorizing a download")
    args = parser.parse_args(argv)
    try:
        model, projection = resolve_model(args.catalog, args.file, args.url, args.sha256, args.model_id)
        if args.show_json:
            print(json.dumps({**projection, "artifact": artifact_identity(model)}, indent=2))
            return 0
        display(projection, artifact_identity(model), sys.stderr)
        if args.ack_file:
            acknowledgement = receipt_acknowledgement(args.ack_file, model)
        elif args.non_interactive:
            raise ValueError("An explicit digest-bound --ack-file is required for an unattended download.")
        else:
            acknowledgement = interactive_acknowledgement(projection, sys.stdin, sys.stderr)
        error = download_review_error(model, acknowledgement)
        if error:
            raise ValueError(error["code"] + ": " + error["error"])
        # Re-read after the prompt, so changed artifacts/terms cannot inherit
        # approval of the record that was displayed before the operator replied.
        current, _ = resolve_model(args.catalog, args.file, args.url, args.sha256, args.model_id)
        error = download_review_error(current, acknowledgement)
        if error:
            raise ValueError(error["code"] + ": " + error["error"])
        if args.write_ack_file:
            write_receipt(args.write_ack_file, current, acknowledgement)
        print("Explicit download review confirmed for " + model["id"], file=sys.stderr)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("Model download blocked: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
