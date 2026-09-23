"""Recognize paired historical scanner records without exempting their files."""
from collections import defaultdict
from pathlib import Path
import re


FINGERPRINT_FILE = ".gitleaksignore"
LEDGER_FILE = "ods/docs/SECRET_SCAN_REVIEW.md"
IDENTITY = r"(?P<commit>[0-9a-f]{40})"
SOURCE_PATH = r"(?P<path>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)"
RULE = r"(?P<rule>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)"
LINE = r"(?P<line>[1-9][0-9]*)"
FINGERPRINT = re.compile(IDENTITY + ":" + SOURCE_PATH + ":" + RULE + ":" + LINE)
LEDGER_ROW = re.compile(
    r"\| `" + IDENTITY + r"` \| `" + SOURCE_PATH + ":" + LINE
    + r"` \| `" + RULE + r"` \| (?P<reason>[^|`\r\n]+) \|"
)


def _records(text, pattern, historical_root):
    records = defaultdict(list)
    for number, line in enumerate(text.splitlines(), start=1):
        match = pattern.fullmatch(line)
        if not match:
            continue
        parts = match["path"].split("/")
        if (parts[0] != historical_root or any(part in {".", ".."} for part in parts)
                or (pattern is LEDGER_ROW and not match["reason"].strip())):
            continue
        key = tuple(match[field] for field in ("commit", "path", "rule", "line"))
        # The reason, rule, commit and surrounding text remain subject to the
        # caller's retired-name guard. Only the historical path is evidence.
        masked = line[:match.start("path")] + "[historical source path]" + line[match.end("path"):]
        records[key].append((number, masked))
    return records


def historical_line_masks(repo_root, historical_root):
    """Return masks only for unique, exact fingerprint/ledger pairs.

    A syntactically valid hash is an evidence identifier, not a claim of secret
    revocation or a verified Git object. CI may use a shallow checkout; source
    adjudication remains the separate scanner ledger's responsibility.
    """
    root = Path(repo_root)
    ignore = root / FINGERPRINT_FILE
    ledger = root / LEDGER_FILE
    if not ignore.is_file() or not ledger.is_file():
        return {}
    fingerprints = _records(ignore.read_text(encoding="utf-8"), FINGERPRINT, historical_root)
    rows = _records(ledger.read_text(encoding="utf-8"), LEDGER_ROW, historical_root)
    masks = {}
    for key in fingerprints.keys() & rows.keys():
        if len(fingerprints[key]) != 1 or len(rows[key]) != 1:
            continue
        for filename, records in ((FINGERPRINT_FILE, fingerprints), (LEDGER_FILE, rows)):
            number, masked = records[key][0]
            masks[(filename, number)] = masked
    return masks
