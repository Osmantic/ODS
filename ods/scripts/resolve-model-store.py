#!/usr/bin/env python3
"""Return the validated active model paths/profile as JSON; never launch it."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'extensions/services/dashboard-api'))
from model_stores import resolve_runtime_selection

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--verify-artifacts', action='store_true', help='Hash qualified artifacts before starting inference')
    parser.add_argument('--allow-missing-model', action='store_true', help='Return typed ownership metadata for status/stop only; never for launching')
    args = parser.parse_args()
    if args.verify_artifacts and args.allow_missing_model:
        parser.error('--verify-artifacts cannot use --allow-missing-model')
    try:
        print(json.dumps(resolve_runtime_selection(args.install_dir, verify_hashes=args.verify_artifacts, allow_missing_model=args.allow_missing_model)))
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
