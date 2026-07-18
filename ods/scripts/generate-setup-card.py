#!/usr/bin/env python3
"""
generate-setup-card.py — produce a printable setup card PNG for a ODS unit.

Use this when shipping a unit: pre-configure its setup-mode Wi-Fi AP (a unique
SSID + password per device), feed those creds plus the setup URL into this
script, get back a 4×6 portrait card you can print + laminate + drop in the box.

The card carries:
  * Top: "ODS" wordmark + the device's mDNS name (e.g. "ods.local")
  * Two big QR codes:
      - Left:  Wi-Fi join QR (Android + iOS recognize the WIFI:T:...;S:...;P:...;; format)
      - Right: setup URL — opens straight to the first-boot wizard
  * Plain-text fallback at the bottom (SSID / password / URL) for the
    inevitable phone that won't auto-detect the QR
  * Optional serial / batch line for fulfillment tracking

This is a tooling artifact, not a runtime feature. It only needs to run on
the operator's machine (or the fulfillment pipeline), not on the device itself.

Usage:
    python3 generate-setup-card.py \\
        --ssid 'ODS-Setup-A4F2'    \\
        --password 'xxxxxxxx'         \\
        --setup-url 'http://192.168.7.1/setup' \\
        --device-name 'ods.local'   \\
        --serial 'DRM-2026-A4F2'      \\
        --output card-A4F2.png

    python3 generate-setup-card.py \\
        --mode factory-owner \\
        --ssid 'ODS-Setup-A4F2' \\
        --password 'xxxxxxxx' \\
        --owner-url 'http://auth.ods-a4f2.local/magic-link/...' \\
        --device-name 'ods-a4f2.local' \\
        --output owner-card-A4F2.pdf

Requires: Pillow + qrcode. Imports lazily so `--help` works without them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Card geometry — 4×6 inches @ 300 DPI = 1200×1800px portrait.
CARD_W = 1200
CARD_H = 1800
MARGIN = 80

# Brand-ish palette. Matches the dashboard's dark theme but printable.
COLOR_BG = (15, 15, 19)          # near-black, but not pure black (prints better)
COLOR_FG = (228, 228, 231)        # near-white
COLOR_ACCENT = (167, 139, 250)    # purple, matches --theme-accent
COLOR_MUTED = (140, 140, 150)


def build_wifi_qr_payload(ssid: str, password: str, security: str = "WPA") -> str:
    """Return the standard Wi-Fi join URI Android/iOS will recognize.

    Format: WIFI:T:<security>;S:<ssid>;P:<password>;H:false;;

    Special characters in SSID/password must be escaped (\\:, \\;, \\\\, \\").
    """
    def esc(s: str) -> str:
        return (
            s.replace("\\", "\\\\")
             .replace(";", "\\;")
             .replace(",", "\\,")
             .replace(":", "\\:")
             .replace('"', '\\"')
        )

    effective_security = "nopass" if not password else security
    payload = f"WIFI:T:{effective_security};S:{esc(ssid)};"
    if effective_security != "nopass" and password:
        payload += f"P:{esc(password)};"
    payload += "H:false;;"
    return payload


def render_qr(text: str, target_px: int):
    """Return a Pillow Image of the QR sized to ~target_px x target_px."""
    import qrcode  # noqa: PLC0415 — lazy import keeps --help fast
    from PIL import Image  # noqa: PLC0415
    from qrcode.constants import ERROR_CORRECT_M

    # ERROR_CORRECT_M handles ~15% damage which is fine for a printed card.
    # box_size is the pixel size of each "module" (QR cell); we scale up.
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=10,
        # Keep the spec-recommended four-module quiet zone. The QR sits on a
        # dark card background, so this white margin is the only separator a
        # scanner sees at the code edge.
        border=4,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    # qrcode picks the cell size; final image dimensions vary by data length.
    # We rescale to the target so the card layout is predictable.
    #
    # NEAREST is critical: the default resampling (BICUBIC) antialiases the
    # cell edges, which turns the pure black/white QR into ~190 grayscale
    # colors. That still scans in many cases, but printed cards must be
    # crisp — a phone camera in suboptimal lighting can fail on the
    # antialiased version. NEAREST keeps every pixel pure black or pure
    # white, matching the source modules exactly.
    return img.resize((target_px, target_px), resample=Image.Resampling.NEAREST)


def render_card(
    ssid: str,
    password: str,
    setup_url: str | None,
    device_name: str,
    security: str = "WPA",
    serial: str | None = None,
    mode: str = "setup",
    owner_url: str | None = None,
):
    """Compose the full card image. Returns a Pillow Image."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    if mode not in {"setup", "factory-owner"}:
        raise ValueError("mode must be setup or factory-owner")
    right_url = owner_url if mode == "factory-owner" else setup_url
    if not right_url:
        raise ValueError("--owner-url is required for factory-owner mode" if mode == "factory-owner" else "--setup-url is required")
    right_caption = "2. OPEN ODS TALK" if mode == "factory-owner" else "2. OPEN SETUP"
    tagline = "Scan to join. Scan to talk." if mode == "factory-owner" else "Scan to set up. Scan to chat."
    fallback_url_label = "owner url" if mode == "factory-owner" else "then visit"

    card = Image.new("RGB", (CARD_W, CARD_H), COLOR_BG)
    draw = ImageDraw.Draw(card)

    title_font = _load_font(size=80, bold=True)
    heading_font = _load_font(size=42, bold=True)
    body_font = _load_font(size=32)
    small_font = _load_font(size=24)
    # Note: the monospace value font is no longer eagerly loaded — the
    # password-overflow fix (auto-shrinking via _fit_font_to_width) picks
    # a size per-row instead of using a single fixed mono_font.

    # --- Header band ---
    draw.text(
        (MARGIN, MARGIN),
        "ODS",
        font=title_font,
        fill=COLOR_ACCENT,
    )
    draw.text(
        (MARGIN, MARGIN + 100),
        device_name,
        font=heading_font,
        fill=COLOR_FG,
    )
    draw.text(
        (MARGIN, MARGIN + 160),
        tagline,
        font=body_font,
        fill=COLOR_MUTED,
    )

    # --- QR pair ---
    qr_size = (CARD_W - MARGIN * 3) // 2  # two QRs + margin between
    qr_y = 400
    wifi_qr = render_qr(build_wifi_qr_payload(ssid, password, security), qr_size)
    url_qr = render_qr(right_url, qr_size)
    card.paste(wifi_qr, (MARGIN, qr_y))
    card.paste(url_qr, (MARGIN * 2 + qr_size, qr_y))

    # QR captions
    draw.text(
        (MARGIN, qr_y + qr_size + 20),
        "1. JOIN WI-FI",
        font=heading_font,
        fill=COLOR_ACCENT,
    )
    draw.text(
        (MARGIN * 2 + qr_size, qr_y + qr_size + 20),
        right_caption,
        font=heading_font,
        fill=COLOR_ACCENT,
    )

    # --- Plain-text fallback block ---
    fallback_y = qr_y + qr_size + 130
    draw.text(
        (MARGIN, fallback_y),
        "if a QR won't scan:",
        font=small_font,
        fill=COLOR_MUTED,
    )

    rows = [
        ("network", ssid),
        ("password", password if password else "(open)"),
        (fallback_url_label, right_url),
    ]
    # The value column starts at x=MARGIN+240 and must fit within the right
    # margin (CARD_W - MARGIN). Anything wider gets shrunk to fit OR wrapped
    # across lines. Max-length WPA2 passwords (63 chars) and long mDNS URLs
    # both blow past the default mono font width otherwise — and a setup
    # card where the password runs off the right edge defeats the point of
    # having a fallback block.
    value_x = MARGIN + 240
    value_max_width = CARD_W - MARGIN - value_x  # pixels available
    row_y = fallback_y + 50
    for label, value in rows:
        draw.text((MARGIN, row_y), label.upper(), font=small_font, fill=COLOR_MUTED)
        value_font = _fit_font_to_width(draw, value, value_max_width, base_size=36, min_size=18, monospace=True)
        draw.text(
            (value_x, row_y - 6),
            value,
            font=value_font,
            fill=COLOR_FG,
        )
        row_y += 70

    # --- Footer / serial ---
    footer_y = CARD_H - MARGIN - 30
    draw.text(
        (MARGIN, footer_y),
        "ODS is open-source — osmantic.com",
        font=small_font,
        fill=COLOR_MUTED,
    )
    if serial:
        bbox = draw.textbbox((0, 0), serial, font=small_font)
        text_w = bbox[2] - bbox[0]
        draw.text(
            (CARD_W - MARGIN - text_w, footer_y),
            serial,
            font=small_font,
            fill=COLOR_MUTED,
        )

    return card


def _fit_font_to_width(draw, text: str, max_width: int, base_size: int = 36,
                       min_size: int = 18, monospace: bool = False):
    """Return the largest font (at or below ``base_size``) whose ``text``
    measures ``<= max_width`` pixels. Floors at ``min_size`` even if the
    text is still wider, on the principle that a readable
    fallback is more useful than a value clipped to the next-line.

    Needed for the password row — WPA2 supports up to 63 characters, and
    a 36-pt monospace render of that is wider than the available column.
    Without auto-shrink, the value runs off the right edge of the card.
    """
    for size in range(base_size, min_size - 1, -2):
        font = _load_font(size=size, monospace=monospace)
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            width = bbox[2] - bbox[0]
        except (AttributeError, OSError):
            # Pillow's fallback bitmap font doesn't honor textbbox cleanly
            # on every platform — assume it fits and return base size.
            return font
        if width <= max_width:
            return font
    # Floor: return the smallest size and accept the overflow as a last resort.
    return _load_font(size=min_size, monospace=monospace)


def _load_font(size: int, bold: bool = False, monospace: bool = False):
    """Best-effort font loader. Falls back to Pillow's default bitmap font
    if no truetype font is available — the card still renders, just less
    pretty. The card is meant to be printed, so we look for common system
    fonts first.
    """
    from PIL import ImageFont  # noqa: PLC0415

    candidates: list[str] = []
    if monospace:
        candidates += [
            "C:\\Windows\\Fonts\\consola.ttf",   # Windows Consolas
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/System/Library/Fonts/Menlo.ttc",
        ]
    elif bold:
        candidates += [
            "C:\\Windows\\Fonts\\arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    else:
        candidates += [
            "C:\\Windows\\Fonts\\arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]

    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a printable setup or factory-owner card for a ODS unit.",
    )
    parser.add_argument("--mode", default="setup", choices=["setup", "factory-owner"],
                        help="Card mode. setup prints Wi-Fi + setup URL; factory-owner prints Wi-Fi + owner ODS Talk QR")
    parser.add_argument("--ssid", required=True, help="Wi-Fi SSID of the device's setup AP")
    parser.add_argument("--password", default="", help="Wi-Fi password (empty for open network)")
    parser.add_argument("--security", default="WPA", choices=["WPA", "WEP", "nopass"],
                        help="Wi-Fi security type (default WPA)")
    parser.add_argument("--setup-url", default=None,
                        help="URL to open after joining the AP (e.g. http://192.168.7.1/setup)")
    parser.add_argument("--owner-url", default=None,
                        help="Owner magic-link URL for factory-owner cards")
    parser.add_argument("--device-name", default="ods.local",
                        help="The mDNS name printed on the card (default ods.local)")
    parser.add_argument("--serial", default=None,
                        help="Optional serial / batch identifier printed in the footer")
    parser.add_argument("--format", choices=["png", "pdf"], default=None,
                        help="Output format. Defaults to PNG unless the output path ends in .pdf")
    parser.add_argument("--output", "-o", required=True,
                        help="Output path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.security == "nopass" and args.password:
        print("error: --security nopass cannot be combined with --password", file=sys.stderr)
        return 2
    if args.mode == "setup" and not args.setup_url:
        print("error: --setup-url is required in setup mode", file=sys.stderr)
        return 2
    if args.mode == "factory-owner" and not args.owner_url:
        print("error: --owner-url is required in factory-owner mode", file=sys.stderr)
        return 2

    try:
        import PIL  # noqa: F401, PLC0415
        import qrcode  # noqa: F401, PLC0415
    except ImportError as exc:
        print(f"error: missing dependency: {exc.name}. "
              "Install with: pip install 'qrcode[pil]'", file=sys.stderr)
        return 2

    card = render_card(
        ssid=args.ssid,
        password=args.password,
        setup_url=args.setup_url,
        device_name=args.device_name,
        security=args.security,
        serial=args.serial,
        mode=args.mode,
        owner_url=args.owner_url,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_format = args.format or ("pdf" if out_path.suffix.lower() == ".pdf" else "png")
    if out_format == "pdf":
        card.save(out_path, format="PDF", resolution=300.0)
    else:
        card.save(out_path, format="PNG", dpi=(300, 300))
    print(f"wrote {out_path} ({CARD_W}×{CARD_H} @ 300 DPI = 4×6 inches)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
