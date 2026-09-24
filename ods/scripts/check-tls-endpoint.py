#!/usr/bin/env python3
"""Verify an operator-selected TLS endpoint and its leaf expiry budget."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import socket
import ssl


def finite_number(value):
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("must be finite")
    return number


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="Connection host or IP, without a URL scheme")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--server-name", help="TLS SNI and verified name; defaults to host")
    parser.add_argument("--ca-file", help="PEM trust bundle, for example your private CA")
    parser.add_argument("--min-valid-days", type=finite_number, default=7)
    parser.add_argument("--timeout", type=finite_number, default=5,
                        help="Seconds per connection/handshake operation; DNS is not bounded")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.timeout <= 0 or args.min_valid_days < 0:
        parser.error("--timeout must be positive and --min-valid-days nonnegative")
    args.server_name = args.server_name or args.host
    for value in (args.host, args.server_name):
        if not value or any(character.isspace() or character in "/?#\0" for character in value):
            parser.error("host and --server-name must be hostnames or IPs, not URLs")
    return args


def inspect_certificate(connection):
    certificate = connection.getpeercert()
    expires = ssl.cert_time_to_seconds(certificate["notAfter"])
    now = datetime.now(timezone.utc)
    return {
        "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
        "remaining_days": (expires - now.timestamp()) / 86400,
        "sha256": hashlib.sha256(connection.getpeercert(binary_form=True)).hexdigest(),
    }


def probe(args, context, receipt):
    try:
        with socket.create_connection((args.host, args.port), timeout=args.timeout) as raw:
            with context.wrap_socket(raw, server_hostname=args.server_name) as connection:
                receipt["certificate"] = inspect_certificate(connection)
                receipt["tls_version"] = connection.version()
    except ssl.SSLCertVerificationError as error:
        return "certificate_verification_failed", str(error)
    except TimeoutError as error:
        return "timeout", str(error)
    except ssl.SSLError as error:
        return "tls_error", str(error)
    except OSError as error:
        return "connection_error", str(error)
    if receipt["certificate"]["remaining_days"] < args.min_valid_days:
        return "expires_soon", "Verified leaf certificate has less than the required remaining lifetime"
    return "healthy", None


def main():
    args = arguments()
    receipt = {
        "schema_version": 1, "host": args.host, "port": args.port,
        "server_name": args.server_name, "min_valid_days": args.min_valid_days,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "certificate": None, "tls_version": None,
    }
    try:
        context = ssl.create_default_context(cafile=args.ca_file)
    except (OSError, ssl.SSLError) as error:
        reason, detail, exit_code = "ca_configuration_error", str(error), 2
    else:
        reason, detail = probe(args, context, receipt)
        exit_code = 0 if reason == "healthy" else 1
    receipt.update(ok=exit_code == 0, reason=reason, detail=detail)
    print(json.dumps(receipt, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
