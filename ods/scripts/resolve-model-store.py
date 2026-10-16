#!/usr/bin/env python3
"""Return the validated active model paths/profile as JSON; never launch it."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'extensions/services/dashboard-api'))
from model_stores import resolve_runtime_selection


def chat_template_path(install_dir: Path) -> str | None:
    """The fixed chat template the active model runs with (any-model WP5), hash-checked.

    Native macOS restarts pass it as --chat-template-file, as the host agent's
    switch does; a named template that is missing or altered refuses the start.
    """
    from env_values import parse_env_value
    override = None
    for line in (install_dir / '.env').read_text(encoding='utf-8').splitlines():
        key, separator, value = line.partition('=')
        if separator and key.strip() == 'MODEL_CHAT_TEMPLATE_OVERRIDE':
            override = parse_env_value(value).strip() or None
    if override is None:
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'bin'))
    from model_profile import templates
    root = install_dir / 'config' / 'chat-templates'
    entry = templates.entry_by_id(templates.load_index(root), override)
    if entry is None:
        raise ValueError(f'The fixed chat template {override} is not in this installation')
    return str(templates.template_path(root, entry))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--verify-artifacts', action='store_true', help='Hash qualified artifacts before starting inference')
    parser.add_argument('--allow-missing-model', action='store_true', help='Return typed ownership metadata for status/stop only; never for launching')
    args = parser.parse_args()
    if args.verify_artifacts and args.allow_missing_model:
        parser.error('--verify-artifacts cannot use --allow-missing-model')
    try:
        selection = resolve_runtime_selection(args.install_dir, verify_hashes=args.verify_artifacts, allow_missing_model=args.allow_missing_model)
        if args.verify_artifacts:
            selection['chatTemplatePath'] = chat_template_path(args.install_dir)
        print(json.dumps(selection))
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
