"""Chat with Portal from Telegram, through the owner's own bot.

The owner creates a bot with @BotFather and gives Settings its token and their own numeric
Telegram user ID. The service then long-polls that bot (outbound only: nothing listens for
Telegram). Text the owner sends the bot in a private chat goes to Portal as one Portal chat,
through the same ingress socket the dashboard's chat uses, and Portal's answer goes back.
Messages from anyone else, and from groups, are dropped before Portal sees them. A question
card Portal shows (such as "Remember this about you?") arrives as numbered choices, and a
number in reply answers it. /new starts a new conversation.
"""
import http.client
import json
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

from integrations_common import IntegrationError, NETWORK_TIMEOUT, default_telegram, now, open_direct

DEFAULT_API = default_telegram()["apiRoot"]
TOKEN = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,64}$")
OWNER_ID = re.compile(r"^[1-9]\d{4,14}$")
INGRESS = "/run/ods-pixel/pixel-ingress.sock"
POLL_SECONDS = 25
IDLE_SECONDS = 10
RETRY_SECONDS = 30
TYPING_SECONDS = 4.5  # Telegram shows "typing…" for about 5 seconds.
PORTAL_SECONDS = 1900  # Above Portal's own 30-minute limit (pixel_ingress.mjs TOTAL_TIMEOUT_MS).
MAX_MESSAGE = 4000  # Telegram takes up to 4,096 characters per message.
MAX_HISTORY = 20  # Messages of the conversation sent with each new one.
MAX_TEXT = 8000
MAX_REPLY_BYTES = 4 * 1024 * 1024
MAX_REFUSED = 50
OWNER_ACTIONS = {"telegram-save", "telegram-test"}
HELP = ("This is your Portal. Write here as you would in the Portal chat. "
        "/new starts a new conversation.")
TEXT_ONLY = "Portal reads text messages here. Send your request as text."


def public(state, has_token):
    """Telegram for the owner's Settings screen: never the token, the history or who was refused."""
    return {"enabled": state["enabled"], "ownerId": state["ownerId"], "apiRoot": state["apiRoot"], "hasToken": has_token,
            "bot": state["bot"], "lastError": state["lastError"], "lastMessageAt": state["lastMessageAt"],
            "refusedCount": len(state["refused"])}


def _api_root(value):
    if not isinstance(value, str) or len(value) > 300:
        raise IntegrationError("invalid-settings", "Check the Telegram API address.")
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query:
        raise IntegrationError("invalid-settings", "The Telegram API address must be an http or https address.")
    return value.strip().rstrip("/")


def validate(state, request, has_token):
    """The owner's Telegram settings applied to the saved state; has_token says whether a token is saved or given."""
    enabled, owner = request.get("enabled"), request.get("ownerId")
    if not isinstance(enabled, bool) or not isinstance(owner, str):
        raise IntegrationError("invalid-settings", "Check the Telegram settings.")
    owner = owner.strip()
    if owner and not OWNER_ID.match(owner):
        raise IntegrationError("invalid-settings", "Your Telegram user ID is a number, such as 123456789. "
                               "@userinfobot in Telegram tells you yours.")
    if enabled and not (owner and has_token):
        raise IntegrationError("invalid-settings", "Give the bot token and your Telegram user ID, or turn Telegram off.")
    return {**state, "enabled": enabled, "ownerId": owner, "apiRoot": _api_root(request.get("apiRoot", DEFAULT_API))}


def token(value):
    if not isinstance(value, str) or not TOKEN.match(value.strip()):
        raise IntegrationError("invalid-settings", "That is not a bot token. @BotFather gives one such as 123456789:AAH….")
    return value.strip()


def owner_messages(updates, owner_id):
    """The owner's messages to the bot in a private chat, and the ids of anyone else who wrote."""
    messages, strangers = [], []
    for update in updates:
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict):
            continue
        chat, sender = message.get("chat") or {}, message.get("from") or {}
        if str(sender.get("id")) != owner_id or chat.get("type") != "private":
            strangers.append(str(sender.get("id", "unknown")))
            continue
        text = message.get("text")
        messages.append({"chat": chat.get("id"), "text": text if isinstance(text, str) else None})
    return messages, strangers


def next_offset(updates, offset):
    ids = [update["update_id"] for update in updates if isinstance(update, dict) and isinstance(update.get("update_id"), int)]
    return max([offset - 1, *ids]) + 1


def chunks(text):
    """Telegram-sized pieces, split at line ends where possible."""
    text = text.strip() or "(Portal sent an empty answer.)"
    pieces = []
    while len(text) > MAX_MESSAGE:
        cut = text.rfind("\n", 0, MAX_MESSAGE)
        cut = cut if cut > MAX_MESSAGE // 2 else MAX_MESSAGE
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return pieces + [text]


def portal_request(history, text, session):
    return {"model": "openclaw/default", "stream": True, "user": session,
            "messages": [*history, {"role": "user", "content": text}]}


def read_stream(raw):
    """Portal's answer from its event stream: the text, and a question card if it asked one."""
    content, questions = [], None
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.startswith("data:") or line[5:].strip() == "[DONE]":
            continue
        try:
            frame = json.loads(line[5:])
        except ValueError:
            continue
        for choice in frame.get("choices") or []:
            piece = (choice.get("delta") or {}).get("content")
            if isinstance(piece, str):
                content.append(piece)
        asked = (frame.get("pixel_questions") or {}).get("questions")
        if isinstance(asked, list) and asked:
            questions = asked
    return "".join(content), questions


def questions_text(questions):
    """A question card as Telegram text: each question with numbered choices."""
    parts = []
    for question in questions:
        options = "\n".join(f"{number}. {option}" for number, option in enumerate(question["options"], 1))
        parts.append(f"{question['question']}\n{options}")
    hint = "Reply with a number, or write your own answer." if len(questions) == 1 else "Reply with your answers."
    return "\n\n".join(parts) + "\n\n" + hint


def answer_text(questions, reply):
    """The owner's reply to a card, written as the Portal chat writes an answer: the question, then the choice."""
    if not questions or len(questions) != 1:
        return reply
    question, choice = questions[0], reply.strip()
    options = question["options"]
    if choice.isdigit() and 1 <= int(choice) <= len(options):
        choice = options[int(choice) - 1]
    else:
        choice = next((option for option in options if option.casefold() == choice.casefold()), choice)
    return f"{question['question']}\n{choice}"


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def ask_portal(body, ingress=INGRESS, timeout=PORTAL_SECONDS):
    """One Portal turn through its ingress socket; the raw event stream."""
    connection = _UnixConnection(ingress, timeout)
    try:
        connection.request("POST", "/v1/chat/completions", body=json.dumps(body), headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(MAX_REPLY_BYTES + 1)
    except (OSError, http.client.HTTPException):
        raise IntegrationError("portal-unavailable", "Portal is not running or did not answer.") from None
    finally:
        connection.close()
    if response.status != 200:
        busy = response.status in (409, 429, 503)
        raise IntegrationError("portal-unavailable", "Portal is busy; try again in a moment." if busy
                               else f"Portal could not answer ({response.status}).")
    if len(raw) > MAX_REPLY_BYTES:
        raise IntegrationError("portal-unavailable", "Portal's answer was too long to send.")
    return raw


class TelegramActions:
    """Mixed into the service's Integrations class, which provides store and _log."""

    ingress = INGRESS

    def _telegram(self):
        with self.store.lock:
            return self.store.telegram(), self.store.secrets().get("telegramToken", "")

    def _telegram_change(self, change):
        with self.store.lock:
            state = change(self.store.telegram())
            self.store.save_telegram(state)
        return state

    def telegram_view(self):
        state, saved = self._telegram()
        return public(state, bool(saved))

    def telegram_owner(self, action, request):
        if action == "telegram-test":
            state, saved = self._telegram()
            if not saved:
                raise IntegrationError("telegram-no-token", "Save the bot token first.")
            me = self.telegram_api(state, saved, "getMe", {}, NETWORK_TIMEOUT)
            name = me.get("username") or me.get("first_name") or "your bot"
            self._telegram_change(lambda current: {**current, "bot": f"@{name}", "lastError": ""})
            self._log("telegram-test", f"@{name}", "answered")
            return f"Connected to @{name}. Message it from your own Telegram account to talk to Portal."
        with self.store.lock:
            secrets = self.store.secrets()
            given = request.get("token")
            new_token = token(given) if given not in (None, "") else None
            saved = secrets.get("telegramToken", "")
            updated = validate(self.store.telegram(), request, bool(new_token or saved))
            if new_token and new_token != saved:
                # Another bot: start reading its messages from scratch.
                updated = {**updated, "bot": "", "offset": 0, "history": [], "questions": None, "lastError": ""}
                secrets["telegramToken"] = new_token
                self.store.save_secrets(secrets)
            if not updated["enabled"]:
                updated = {**updated, "lastError": ""}
            self.store.save_telegram(updated)
        self._log("telegram-save", "Telegram", "on" if updated["enabled"] else "off")
        return "Telegram saved." + (" Portal now answers your messages to the bot." if updated["enabled"] else "")

    def telegram_api(self, state, bot_token, method, payload, timeout):
        """One Telegram Bot API call; its result, or a refusal in words the owner can act on."""
        request = urllib.request.Request(f"{state['apiRoot']}/bot{bot_token}/{method}", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with open_direct(request, timeout=timeout) as response:
                body = json.loads(response.read(2_000_000))
        except urllib.error.HTTPError as error:
            if error.code in (401, 404):
                raise IntegrationError("telegram-refused", "Telegram refused the bot token. Check it in Settings → Portal integrations.") from None
            if error.code == 409:
                raise IntegrationError("telegram-conflict", "Another program is reading this bot's messages. Stop it, or make a new bot for Portal.") from None
            raise IntegrationError("telegram-failed", f"Telegram answered {error.code}.") from None
        except (urllib.error.URLError, OSError, ValueError):
            raise IntegrationError("telegram-unreachable", "Telegram could not be reached.") from None
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise IntegrationError("telegram-failed", "Telegram did not accept the request.")
        return body["result"]

    def telegram_poll(self):
        """Wait for the bot's next messages and answer the owner's; False when Telegram is off."""
        state, bot_token = self._telegram()
        if not (state["enabled"] and bot_token and state["ownerId"]):
            return False
        updates = self.telegram_api(state, bot_token, "getUpdates",
                                    {"offset": state["offset"], "timeout": POLL_SECONDS, "allowed_updates": ["message"]},
                                    POLL_SECONDS + NETWORK_TIMEOUT)
        if not isinstance(updates, list):
            raise IntegrationError("telegram-failed", "Telegram sent an unreadable answer.")
        messages, strangers = owner_messages(updates, state["ownerId"])
        offset = next_offset(updates, state["offset"])
        # Marked read first: a message that crashes Portal is not asked again forever.
        self._telegram_change(lambda current: {**current, "offset": offset, "lastError": ""})
        for stranger in strangers:
            self._refused(stranger)
        for message in messages:
            self._telegram_message(state, bot_token, message)
        return True

    def _refused(self, sender):
        refused = self._telegram()[0]["refused"]
        if sender in refused:
            return
        self._telegram_change(lambda current: {**current, "refused": (current["refused"] + [sender])[-MAX_REFUSED:]})
        self._log("telegram-refused", "someone else wrote to the bot", "ignored")

    def _send(self, state, bot_token, chat, text):
        for piece in chunks(text):
            self.telegram_api(state, bot_token, "sendMessage", {"chat_id": chat, "text": piece}, NETWORK_TIMEOUT)

    def _telegram_message(self, state, bot_token, message):
        chat, text = message["chat"], message["text"]
        if text is None:
            return self._send(state, bot_token, chat, TEXT_ONLY)
        if text.strip() in ("/start", "/help"):
            return self._send(state, bot_token, chat, HELP)
        if text.strip() == "/new":
            self._telegram_change(lambda current: {**current, "conversation": current["conversation"] + 1, "history": [],
                                                   "questions": None})
            self._log("telegram-message", "new conversation", "started")
            return self._send(state, bot_token, chat, "New conversation started.")
        current = self._telegram()[0]
        sent = answer_text(current["questions"], text[:MAX_TEXT])
        session = f"telegram-{current['ownerId']}-{current['conversation']}"
        done = threading.Event()
        threading.Thread(target=self._typing, args=(state, bot_token, chat, done), daemon=True).start()
        try:
            content, questions = read_stream(ask_portal(portal_request(current["history"], sent, session), self.ingress))
        except IntegrationError as error:
            self._log("telegram-message", "from Telegram", error.message)
            return self._send(state, bot_token, chat, error.message)
        finally:
            done.set()
        reply = content.strip() if not questions else (content.strip() + "\n\n" if content.strip() else "") + questions_text(questions)
        self._telegram_change(lambda latest: {**latest, "lastMessageAt": now(), "questions": questions,
                                              "history": [*latest["history"], {"role": "user", "content": sent},
                                                          {"role": "assistant", "content": content}][-MAX_HISTORY:]})
        self._log("telegram-message", "from Telegram", "answered")
        self._send(state, bot_token, chat, reply)

    def _typing(self, state, bot_token, chat, done):
        while not done.is_set():
            try:
                self.telegram_api(state, bot_token, "sendChatAction", {"chat_id": chat, "action": "typing"}, NETWORK_TIMEOUT)
            except IntegrationError:
                return  # Only the "typing…" sign is lost; the answer is still sent.
            done.wait(TYPING_SECONDS)


def telegram_loop(service, stop):
    """Read the bot's messages until stop is set; a Telegram failure is shown in Settings and tried again."""
    while not stop.is_set():
        try:
            busy = service.telegram_poll()
        except IntegrationError as error:
            told = service._telegram()[0]["lastError"] == error.message
            service._telegram_change(lambda current: {**current, "lastError": error.message})
            if not told:  # Once per problem, not every retry.
                service._log("telegram", "Telegram", error.message)
            stop.wait(RETRY_SECONDS)
            continue
        if not busy:
            stop.wait(IDLE_SECONDS)
