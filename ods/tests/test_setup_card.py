"""Tests for scripts/generate-setup-card.py.

These exercise the CLI end-to-end: invoke the script, verify it writes
a valid PNG, and sanity-check the WiFi-QR payload encoding. Visual
correctness is not asserted — that's eyeball-test territory.

Run with: pytest tests/test_setup_card.py
"""

import importlib.util
import subprocess
import sys
import struct
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "generate-setup-card.py"


def _import_module():
    """Import the script as a module so we can unit-test its helpers."""
    spec = importlib.util.spec_from_file_location("generate_setup_card", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helper: build_wifi_qr_payload
# ---------------------------------------------------------------------------


def test_wifi_qr_payload_basic():
    mod = _import_module()
    p = mod.build_wifi_qr_payload("MyNet", "secret123", "WPA")
    # Order is T;S;P;H — standard.
    assert p == "WIFI:T:WPA;S:MyNet;P:secret123;H:false;;"


def test_wifi_qr_payload_open_network():
    mod = _import_module()
    p = mod.build_wifi_qr_payload("Open", "", "WPA")
    # Password segment omitted entirely when password is empty.
    assert "P:" not in p
    assert p.startswith("WIFI:T:nopass;S:Open;")


def test_wifi_qr_payload_nopass_ignores_empty_password_only():
    mod = _import_module()
    p = mod.build_wifi_qr_payload("Open", "", "nopass")
    assert p == "WIFI:T:nopass;S:Open;H:false;;"


def test_wifi_qr_payload_escapes_special_chars():
    mod = _import_module()
    p = mod.build_wifi_qr_payload("My;Net,wifi", 'pass":word\\', "WPA")
    # Per the WIFI: URI escape rules, all of \, ;, ,, :, " must be
    # backslash-escaped in the value (not just the obvious separators).
    assert "S:My\\;Net\\,wifi" in p
    assert "P:pass\\\"\\:word\\\\" in p


# ---------------------------------------------------------------------------
# CLI invocation
# ---------------------------------------------------------------------------


def _have_pillow_and_qrcode():
    try:
        import PIL  # noqa: F401
        import qrcode  # noqa: F401
        return True
    except ImportError:
        return False


pillow_required = pytest.mark.skipif(
    not _have_pillow_and_qrcode(),
    reason="Pillow + qrcode not installed; setup-card requires them",
)


@pillow_required
def test_cli_writes_valid_png(tmp_path):
    out = tmp_path / "card.png"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--ssid", "ODS-Setup-TEST",
            "--password", "supersecret",
            "--setup-url", "http://192.168.7.1/setup",
            "--device-name", "ods-test.local",
            "--serial", "TEST-001",
            "--output", str(out),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert out.exists()
    assert out.stat().st_size > 1000  # nontrivial PNG

    # PNG magic number — first 8 bytes must be 89 50 4E 47 0D 0A 1A 0A
    with open(out, "rb") as f:
        header = f.read(8)
    assert header == b"\x89PNG\r\n\x1a\n", "output is not a valid PNG"


@pillow_required
def test_cli_writes_factory_owner_png(tmp_path):
    out = tmp_path / "owner-card.png"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--mode", "factory-owner",
            "--ssid", "ODS-Setup-TEST",
            "--password", "supersecret",
            "--owner-url", "http://auth.ods.local/magic-link/owner-token",
            "--device-name", "ods-test.local",
            "--output", str(out),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert out.exists()
    with open(out, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"


@pillow_required
def test_cli_writes_factory_owner_pdf(tmp_path):
    out = tmp_path / "owner-card.pdf"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--mode", "factory-owner",
            "--ssid", "ODS-Setup-TEST",
            "--password", "supersecret",
            "--owner-url", "http://auth.ods.local/magic-link/owner-token",
            "--output", str(out),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert out.exists()
    with open(out, "rb") as f:
        assert f.read(4) == b"%PDF"


@pillow_required
def test_cli_creates_parent_directory(tmp_path):
    nested = tmp_path / "deep" / "subdir" / "card.png"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--ssid", "Open",
            "--security", "nopass",
            "--setup-url", "http://192.168.7.1/setup",
            "--output", str(nested),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert nested.exists()


@pillow_required
def test_cli_dimensions_match_4x6_at_300dpi(tmp_path):
    """The script promises 1200×1800 px at 300 DPI = 4×6 inches."""
    out = tmp_path / "card.png"
    subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--ssid", "x", "--password", "y",
            "--setup-url", "http://192.168.7.1/setup",
            "--output", str(out),
        ],
        check=True, capture_output=True, timeout=30,
    )
    # Parse PNG IHDR to read width/height without importing Pillow here.
    with open(out, "rb") as f:
        f.seek(16)
        width, height = struct.unpack(">II", f.read(8))
    assert (width, height) == (1200, 1800)


def test_cli_requires_ssid():
    """Argparse error when --ssid is missing."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--setup-url", "http://x/", "--output", "/tmp/x.png"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "ssid" in result.stderr.lower()


def test_cli_requires_setup_url():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--ssid", "x", "--output", "/tmp/x.png"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "setup-url" in result.stderr.lower() or "setup_url" in result.stderr.lower()


def test_cli_requires_owner_url_in_factory_owner_mode(tmp_path):
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--mode", "factory-owner",
            "--ssid", "x",
            "--output", str(tmp_path / "owner.png"),
        ],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "owner-url" in result.stderr.lower() or "owner_url" in result.stderr.lower()


def test_cli_help_works_without_pillow():
    """Argparse help shouldn't require Pillow; helpful when an operator is
    checking flags before installing deps."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "ssid" in result.stdout.lower()
    assert "setup-url" in result.stdout.lower()
    assert "owner-url" in result.stdout.lower()


@pillow_required
def test_cli_rejects_nopass_with_password(tmp_path):
    out = tmp_path / "card.png"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--ssid", "Open",
            "--password", "should-not-be-used",
            "--security", "nopass",
            "--setup-url", "http://192.168.7.1/setup",
            "--output", str(out),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert "nopass" in result.stderr
    assert not out.exists()


@pillow_required
def test_qr_has_four_module_quiet_zone():
    mod = _import_module()
    img = mod.render_qr("hello", 240)
    white = (255, 255, 255)
    # With border=4 on a version-1 QR, the top quiet zone is ~33 px after
    # scaling to 240 px. A one-module border is only ~10 px and fails here.
    for x in range(0, 240):
        for y in range(0, 24):
            assert img.getpixel((x, y)) == white


# ---------------------------------------------------------------------------
# Regression: 63-char WPA2 password must fit in the fallback column
# ---------------------------------------------------------------------------


@pillow_required
def test_max_length_wpa_password_fits_in_fallback(tmp_path):
    """Reviewer flagged the plaintext fallback overflowing for max-length
    WPA2 passwords (63 chars). The fix auto-shrinks the mono font to fit
    within the value column; this test renders such a password and inspects
    the output pixels along the right margin to make sure the text didn't
    bleed off the card edge.
    """
    from PIL import Image  # imported only when Pillow is available

    # WPA2 PSK upper bound = 63 chars. Pick a worst-case glyph (wide M's).
    password = "M" * 63
    out = tmp_path / "card-max-password.png"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--ssid", "ODSCard",
            "--password", password,
            "--setup-url", "http://192.168.7.1/setup",
            "--output", str(out),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr

    # Inspect the right-edge column of the password row. Generator puts the
    # password block roughly in the lower-middle of the card; the right
    # margin should be pure background (no glyph pixels). Check a vertical
    # strip 4 px wide along the very right edge in the password band.
    img = Image.open(out).convert("RGB")
    card_w, card_h = img.size
    # Password row sits between ~y=1100 and y=1300 in the layout.
    bg = (15, 15, 19)  # COLOR_BG
    for x in range(card_w - 4, card_w):
        for y in range(1100, 1300):
            assert img.getpixel((x, y)) == bg, (
                f"Pixel at ({x},{y}) is {img.getpixel((x, y))}, "
                "expected card background — password text overflowed the column."
            )
