"""Notifications to the owner's phone or computer through their ntfy server."""
import base64
import urllib.error
import urllib.parse
import urllib.request

from integrations_common import IntegrationError, NETWORK_TIMEOUT


def header_text(value):
    """A header value ntfy shows as written: ASCII as is, anything else RFC 2047 encoded."""
    if value.isascii():
        return value
    return "=?UTF-8?B?" + base64.b64encode(value.encode()).decode() + "?="


def header_link(link):
    """A link fit for a header: characters outside ASCII, spaces and quotes percent-encoded."""
    return urllib.parse.quote(link, safe="!#$%&'()*+,/:;=?@[]~")


def send_notification(config, secrets, title, message, priority="default", link="", link_label="Open approvals"):
    settings = config["notifications"]
    if not settings["enabled"]:
        raise IntegrationError("notifications-off", "Notifications are not set up. The owner can connect ntfy in Settings → Portal integrations.")
    if not isinstance(message, str) or not message.strip() or len(message) > 4000:
        raise IntegrationError("invalid-notification", "Give a message of up to 4,000 characters.")
    title = (title if isinstance(title, str) and title.strip() else "Portal").strip()[:200]
    request = urllib.request.Request(f"{settings['url']}/{settings['topic']}", data=message.strip().encode(), method="POST")
    request.add_header("Title", header_text(title))
    request.add_header("Priority", priority if priority in ("min", "low", "default", "high", "urgent") else "default")
    request.add_header("Tags", "portal")
    if link:
        # Only opens a page (Settings, where the owner still signs in, or a watched page); it grants nothing.
        link = header_link(link)
        request.add_header("Click", link)
        # ntfy splits an action at commas; a quoted link may hold them (its quotes are percent-encoded).
        request.add_header("Actions", f'view, {link_label}, "{link}"')
    password = secrets.get("ntfyPassword")
    if password and settings["username"]:
        request.add_header("Authorization", "Basic " + base64.b64encode(f"{settings['username']}:{password}".encode()).decode())
    elif password:
        request.add_header("Authorization", "Bearer " + password)
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT):
            pass
    except urllib.error.HTTPError as error:
        reason = "the user or password was refused" if error.code in (401, 403) else f"ntfy answered {error.code}"
        raise IntegrationError("notification-failed", f"The notification was not sent: {reason}.") from None
    except (urllib.error.URLError, OSError):
        raise IntegrationError("notification-failed", f"The notification was not sent: ntfy at {settings['url']} could not be reached.") from None
    return title
