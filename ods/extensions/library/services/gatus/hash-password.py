#!/usr/bin/env python3
"""Generate Gatus's native credential locally; requires bcrypt==5.0.0."""

import base64
import getpass

import bcrypt


def main():
    password = getpass.getpass("Gatus password (16-72 UTF-8 bytes): ").encode("utf-8")
    if not 16 <= len(password) <= 72:
        raise SystemExit("Password must contain 16-72 UTF-8 bytes")
    if getpass.getpass("Confirm password: ").encode("utf-8") != password:
        raise SystemExit("Passwords do not match")
    print(base64.urlsafe_b64encode(bcrypt.hashpw(password, bcrypt.gensalt(rounds=12))).decode("ascii"))


if __name__ == "__main__":
    main()
