"""Contract tests for bin/dream-mdns.py's per-subdomain A-record set.

The mDNS announcer publishes a fixed set of `<sub>.<device>.local` A records
so phones on the LAN can resolve the proxy-routed hostnames the magic-link
redemption flow points them at. If a backend service ships a new public host
(see #1319 → talk.<device>.local) but the announcer's `subdomain_routes`
tuple isn't updated, the phone falls off a cliff: the URL exists in Caddy,
magic_link.py points users at it, redemption succeeds, then the browser
can't resolve the host and the user sees a white screen.

These tests guard the subdomain list against silent regressions of that
class. They are static-source assertions (no zeroconf import) so they run
in CI without needing a network bound.

Run with: pytest tests/test_mdns_subdomains.py
"""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MDNS_SCRIPT = REPO_ROOT / "bin" / "dream-mdns.py"

# The set of subdomains the announcer must publish. Each name corresponds to
# a Caddy block in extensions/services/dream-proxy/Caddyfile and (for the
# auth-bound ones) a redirect target in dashboard-api/routers/magic_link.py.
#
# When adding a new public subdomain, update *all three* sources in the same
# PR: the Caddyfile route, the announcer here, and this test. The test is the
# tripwire if any of the three is forgotten.
REQUIRED_SUBDOMAINS = {
    "root",       # bare <device>.local — redirects to chat
    "chat",       # Open WebUI
    "dashboard",  # operator UI
    "auth",       # magic-link redemption (set-cookie + 302)
    "api",        # dashboard-api admin surface
    "hermes",     # hermes-proxy
    "talk",       # mobile owner portal (#1319) — without this, phone scans land on white screen
}


def _extract_subdomain_routes_keys() -> set[str]:
    """Parse dream-mdns.py and return the set of subdomain names it announces.

    We walk the AST instead of executing the script (which would need
    zeroconf + a network interface). Looks for the `subdomain_routes`
    assignment and pulls the first element of each tuple.
    """
    source = MDNS_SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "subdomain_routes" not in targets:
            continue
        value = node.value
        if not isinstance(value, ast.Tuple):
            continue
        for elt in value.elts:
            if isinstance(elt, ast.Tuple) and elt.elts:
                first = elt.elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.add(first.value)
    return found


def test_announcer_publishes_all_required_subdomains() -> None:
    found = _extract_subdomain_routes_keys()
    missing = REQUIRED_SUBDOMAINS - found
    assert not missing, (
        f"bin/dream-mdns.py is missing required subdomains: {sorted(missing)}. "
        "If you added a new public host, add it here AND in subdomain_routes. "
        "If you removed a host on purpose, update REQUIRED_SUBDOMAINS in this test."
    )


def test_announcer_does_not_advertise_unknown_subdomains() -> None:
    # Fail loudly when the announcer publishes a name nobody else expects.
    # Catches drift in the opposite direction: stale entries that point at
    # backends that no longer exist still draw LAN traffic toward Caddy and
    # produce confusing 404s.
    found = _extract_subdomain_routes_keys()
    extra = found - REQUIRED_SUBDOMAINS
    assert not extra, (
        f"bin/dream-mdns.py publishes subdomains not listed in REQUIRED_SUBDOMAINS: "
        f"{sorted(extra)}. Add them to the set above (and confirm Caddy + magic_link "
        "agree) or remove them from subdomain_routes."
    )


if __name__ == "__main__":
    test_announcer_publishes_all_required_subdomains()
    test_announcer_does_not_advertise_unknown_subdomains()
    print("OK")
