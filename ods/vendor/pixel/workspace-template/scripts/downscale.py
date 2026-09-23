#!/usr/bin/env python3
"""Create a bounded-size copy of an image before local vision processing."""
from argparse import ArgumentParser
from pathlib import Path

parser = ArgumentParser()
parser.add_argument("input", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--max-side", type=int, default=1600)
args = parser.parse_args()
try:
    from PIL import Image, ImageOps
except ImportError as error:
    raise SystemExit("Install Pillow first: use the Pixel sandbox image or install python3-pil") from error
if args.max_side < 64:
    raise SystemExit("--max-side must be at least 64")
with Image.open(args.input) as source:
    image = ImageOps.exif_transpose(source)
    image.thumbnail((args.max_side, args.max_side), Image.Resampling.LANCZOS)
    if image.mode not in {"RGB", "RGBA"}: image = image.convert("RGB")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, optimize=True)
print(args.output)
