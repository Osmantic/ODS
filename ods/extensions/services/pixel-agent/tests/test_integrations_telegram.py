import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires Unix sockets; run under Linux/WSL")

import http.server
import json
import pathlib
import socketserver
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "host"))
import integrations_service as service  # noqa: E402
import integrations_telegram as telegram  # noqa: E402
from integrations_common import IntegrationError, Store  # noqa: E402

OWNER = "123456789"
TOKEN = "987654321:AAH" + "x" * 32
CARD = [{"id": "memory_save", "question": 'Remember this about you? "The owner is vegetarian."',
         "options": ["Save", "Don't save", "Always save without asking"]}]


def update(update_id, text, sender=OWNER, chat_type="private"):
    return {"update_id": update_id, "message": {"message_id": update_id, "from": {"id": int(sender)},
                                                 "chat": {"id": int(sender), "type": chat_type}, **({"text": text} if text is not None else {})}}


class FakeTelegram(http.server.BaseHTTPRequestHandler):
    """The Bot API calls the bridge makes: getMe, getUpdates, sendMessage and sendChatAction."""
    token, updates, sent, offsets, status, refuse_sends = TOKEN, [], [], [], 200, 0

    def do_POST(self):
        token, method = self.path.split("/")[1][3:], self.path.split("/")[2]
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if token != FakeTelegram.token or FakeTelegram.status != 200:
            return self.reply({"ok": False}, FakeTelegram.status if FakeTelegram.status != 200 else 401)
        if method == "getMe":
            return self.reply({"ok": True, "result": {"id": 987654321, "username": "portal_test_bot"}})
        if method == "getUpdates":
            FakeTelegram.offsets.append(body["offset"])
            waiting = [item for item in FakeTelegram.updates if item["update_id"] >= body["offset"]]
            return self.reply({"ok": True, "result": waiting})
        if method == "sendMessage":
            if FakeTelegram.refuse_sends:  # Telegram's flood limit.
                FakeTelegram.refuse_sends -= 1
                return self.reply({"ok": False, "description": "Too Many Requests"}, 429)
            FakeTelegram.sent.append((body["chat_id"], body["text"]))
        return self.reply({"ok": True, "result": True})

    def reply(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


class FakeIngress(http.server.BaseHTTPRequestHandler):
    """Portal's ingress: records each request and answers as Portal's event stream does."""
    requests, answer, questions, status = [], "Hello from Portal.", None, 200

    def do_POST(self):
        FakeIngress.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        if FakeIngress.status != 200:
            self.send_response(FakeIngress.status)
            self.end_headers()
            return
        frames = [{"choices": [{"delta": {"role": "assistant"}}]},
                  {"choices": [{"delta": {"content": FakeIngress.answer[:5]}}]},
                  {"choices": [{"delta": {"content": FakeIngress.answer[5:]}}]},
                  {"choices": [{"delta": {}, "finish_reason": "stop"}],
                   **({"pixel_questions": {"schemaVersion": 1, "questions": FakeIngress.questions}} if FakeIngress.questions else {})}]
        body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *_):
        pass


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        return request, ("local", 0)


class Pure(unittest.TestCase):
    def test_settings_need_a_numeric_owner_and_a_token_to_turn_on(self):
        state = Store(tempfile.mkdtemp()).telegram()
        on = telegram.validate(state, {"enabled": True, "ownerId": f" {OWNER} ", "apiRoot": "https://api.telegram.org/"}, True)
        self.assertEqual((on["enabled"], on["ownerId"], on["apiRoot"]), (True, OWNER, "https://api.telegram.org"))
        refusals = [({"enabled": True, "ownerId": OWNER}, False, "Give the bot token"),
                    ({"enabled": True, "ownerId": ""}, True, "Give the bot token"),
                    ({"enabled": False, "ownerId": "@me"}, True, "is a number"),
                    ({"enabled": False, "ownerId": "0123"}, True, "is a number"),
                    ({"enabled": "yes", "ownerId": OWNER}, True, "Check the Telegram settings"),
                    ({"enabled": False, "ownerId": OWNER, "apiRoot": "ftp://x"}, True, "http or https"),
                    ({"enabled": False, "ownerId": OWNER, "apiRoot": "https://user:pw@x"}, True, "http or https")]
        for request, has_token, reason in refusals:
            with self.assertRaises(IntegrationError, msg=request) as caught:
                telegram.validate(state, request, has_token)
            self.assertIn(reason, caught.exception.message)
        self.assertEqual(telegram.token(f" {TOKEN} "), TOKEN)
        for bad in ("", "abc", "123:short", "x" * 80, None):
            with self.assertRaises(IntegrationError):
                telegram.token(bad)

    def test_only_the_owner_s_private_messages_reach_portal(self):
        updates = [update(1, "Hi"), update(2, "Hi", sender="555555"), update(3, "In a group", chat_type="group"),
                   update(4, None), {"update_id": 5, "edited_message": {}}, "junk"]
        messages, strangers = telegram.owner_messages(updates, OWNER)
        self.assertEqual(messages, [{"chat": int(OWNER), "text": "Hi"}, {"chat": int(OWNER), "text": None}])
        self.assertEqual(strangers, ["555555", OWNER])
        self.assertEqual(telegram.next_offset(updates, 0), 6)
        self.assertEqual(telegram.next_offset([], 7), 7)

    def test_long_answers_are_split_for_telegram(self):
        self.assertEqual(telegram.chunks("  short  "), ["short"])
        self.assertEqual(telegram.chunks(""), ["(Portal sent an empty answer.)"])
        long = ("line of text\n" * 700).strip()
        pieces = telegram.chunks(long)
        self.assertTrue(all(len(piece) <= telegram.MAX_MESSAGE for piece in pieces))
        self.assertEqual("\n".join(pieces), long)
        self.assertEqual("".join(telegram.chunks("x" * 9000)), "x" * 9000)

    def test_portal_s_stream_and_question_cards(self):
        raw = (b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
               b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\ndata: not json\n\n'
               b'data: {"choices":[{"delta":{}}],"pixel_questions":{"schemaVersion":1,"questions":'
               + json.dumps(CARD).encode() + b'}}\n\ndata: [DONE]\n\n')
        self.assertEqual(telegram.read_stream(raw), ("Hello", CARD))
        self.assertEqual(telegram.questions_text(CARD), 'Remember this about you? "The owner is vegetarian."\n1. Save\n2. Don\'t save\n'
                         "3. Always save without asking\n\nReply with a number, or write your own answer.")
        self.assertEqual(telegram.answer_text(CARD, " 1 "), 'Remember this about you? "The owner is vegetarian."\nSave')
        self.assertEqual(telegram.answer_text(CARD, "don't save"), 'Remember this about you? "The owner is vegetarian."\nDon\'t save')
        self.assertEqual(telegram.answer_text(CARD, "9"), 'Remember this about you? "The owner is vegetarian."\n9')
        self.assertEqual(telegram.answer_text(None, "1"), "1")
        self.assertEqual(telegram.answer_text(CARD * 2, "1"), "1")
        self.assertEqual(telegram.telegram_reply(" Hello ", None), "Hello")
        self.assertEqual(telegram.telegram_reply("", CARD), telegram.questions_text(CARD))
        # The model often writes the card out itself; Telegram gets it once, as numbered choices.
        repeated = 'Remember this about you? "The owner is vegetarian."\n- Save\n- Don\'t save'
        self.assertEqual(telegram.telegram_reply(repeated, CARD), telegram.questions_text(CARD))
        self.assertEqual(telegram.telegram_reply("Noted.", CARD), "Noted.\n\n" + telegram.questions_text(CARD))
        # An answer that also quotes the card keeps the answer; the card comes once, as numbered choices.
        answer = 'Try a chickpea curry tonight.\n\nRemember this about you? "The owner is vegetarian."'
        self.assertEqual(telegram.telegram_reply(answer, CARD), "Try a chickpea curry tonight.\n\n" + telegram.questions_text(CARD))
        listed = 'Here is a recipe: lentil soup.\nRemember this about you? "The owner is vegetarian."\n1. Save\n2. Don\'t save'
        self.assertEqual(telegram.telegram_reply(listed, CARD), "Here is a recipe: lentil soup.\n\n" + telegram.questions_text(CARD))
        self.assertEqual(telegram.portal_request("b", "telegram-1-1"),
                         {"model": "openclaw/default", "stream": True, "user": "telegram-1-1",
                          "messages": [{"role": "user", "content": "b"}]})


class Bridge(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(pathlib.Path(temporary.name) / "state")
        self.service = service.Integrations(self.store)
        self.telegram = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeTelegram)
        threading.Thread(target=self.telegram.serve_forever, daemon=True).start()
        self.addCleanup(self.telegram.server_close)
        self.addCleanup(self.telegram.shutdown)
        self.service.ingress = str(pathlib.Path(temporary.name) / "ingress.sock")
        self.ingress = UnixHTTPServer(self.service.ingress, FakeIngress)
        threading.Thread(target=self.ingress.serve_forever, daemon=True).start()
        self.addCleanup(self.ingress.server_close)
        self.addCleanup(self.ingress.shutdown)
        FakeTelegram.token, FakeTelegram.updates, FakeTelegram.sent, FakeTelegram.offsets, FakeTelegram.status = TOKEN, [], [], [], 200
        FakeTelegram.refuse_sends = 0
        FakeIngress.requests, FakeIngress.answer, FakeIngress.questions, FakeIngress.status = [], "Hello from Portal.", None, 200
        self.api = f"http://127.0.0.1:{self.telegram.server_address[1]}"

    def owner(self, action, **fields):
        return self.service.handle("owner", {"schemaVersion": 1, "action": action, **fields})

    def turn_on(self):
        return self.owner("telegram-save", enabled=True, ownerId=OWNER, apiRoot=self.api, token=TOKEN)

    def poll(self, *updates):
        FakeTelegram.updates.extend(updates)
        return self.service.telegram_poll()

    def test_settings_keep_the_token_secret_and_test_names_the_bot(self):
        view = self.turn_on()
        self.assertEqual(view["telegram"], {"enabled": True, "ownerId": OWNER, "apiRoot": self.api, "hasToken": True, "bot": "",
                                            "lastError": "", "lastMessageAt": 0, "refusedCount": 0})
        self.assertNotIn(TOKEN, json.dumps(view))
        self.assertEqual(self.store.secrets()["telegramToken"], TOKEN)
        self.assertEqual(self.owner("telegram-test")["notice"], "Connected to @portal_test_bot. Message it from your own Telegram account to talk to Portal.")
        self.assertEqual(self.owner("get")["telegram"]["bot"], "@portal_test_bot")
        # Saving again without a token keeps the saved one.
        self.owner("telegram-save", enabled=False, ownerId=OWNER, apiRoot=self.api)
        self.assertEqual(self.store.secrets()["telegramToken"], TOKEN)
        with self.assertRaises(IntegrationError):
            self.service.handle("agent", {"schemaVersion": 1, "action": "telegram-save", "enabled": True, "ownerId": OWNER})

    def test_a_refused_token_is_said_plainly(self):
        self.turn_on()
        FakeTelegram.token = "other"
        with self.assertRaises(IntegrationError) as caught:
            self.owner("telegram-test")
        self.assertIn("Telegram refused the bot token", caught.exception.message)

    def test_the_owner_talks_to_portal_and_strangers_are_ignored(self):
        self.turn_on()
        self.assertTrue(self.poll(update(10, "What is on today?"), update(11, "Let me in", sender="555555")))
        self.assertEqual(FakeTelegram.sent, [(int(OWNER), "Hello from Portal.")])
        self.assertEqual(len(FakeIngress.requests), 1)
        request = FakeIngress.requests[0]
        self.assertEqual((request["user"], request["stream"], request["messages"]),
                         (f"telegram-{OWNER}-1", True, [{"role": "user", "content": "What is on today?"}]))
        activity = [(entry["action"], entry["outcome"]) for entry in self.owner("get")["activity"]]
        self.assertIn(("telegram-refused", "ignored"), activity)
        self.assertEqual(self.owner("get")["telegram"]["refusedCount"], 1)
        # Read once: the next poll starts after both.
        self.poll()
        self.assertEqual(FakeTelegram.offsets[-1], 12)
        self.assertEqual(len(FakeIngress.requests), 1)

    def test_a_new_owner_or_bot_forgets_who_was_refused(self):
        # The owner wrote before saving their own ID (or a test left strangers behind): the
        # "someone else wrote" note must not outlive the settings it was about.
        self.owner("telegram-save", enabled=True, ownerId="999999999", apiRoot=self.api, token=TOKEN)
        self.poll(update(10, "hi"))
        self.assertEqual(self.owner("get")["telegram"]["refusedCount"], 1)
        self.owner("telegram-save", enabled=True, ownerId="999999999", apiRoot=self.api)
        self.assertEqual(self.owner("get")["telegram"]["refusedCount"], 1, "saving the same settings keeps the note")
        self.owner("telegram-save", enabled=True, ownerId=OWNER, apiRoot=self.api)
        self.assertEqual(self.owner("get")["telegram"]["refusedCount"], 0)
        self.poll(update(11, "hi again"))
        self.assertEqual(FakeTelegram.sent, [(int(OWNER), "Hello from Portal.")])
        self.poll(update(12, "Let me in", sender="555555"))
        self.assertEqual(self.owner("get")["telegram"]["refusedCount"], 1)
        other = TOKEN.replace(TOKEN.split(":")[1][:4], "ZZZZ", 1)
        FakeTelegram.token = other
        self.owner("telegram-save", enabled=True, ownerId=OWNER, apiRoot=self.api, token=other)
        self.assertEqual(self.owner("get")["telegram"]["refusedCount"], 0)

    def test_the_conversation_continues_and_new_starts_another(self):
        self.turn_on()
        self.poll(update(1, "My name is Ana."))
        FakeIngress.answer = "Nice to meet you."
        self.poll(update(2, "What is my name?"))
        # Portal keeps the conversation in its session: the same user, and only the new message.
        self.assertEqual(FakeIngress.requests[1]["user"], FakeIngress.requests[0]["user"])
        self.assertEqual(FakeIngress.requests[1]["messages"], [{"role": "user", "content": "What is my name?"}])
        self.poll(update(3, "/new"))
        self.assertEqual(FakeTelegram.sent[-1], (int(OWNER), "New conversation started."))
        self.poll(update(4, "Hello again"))
        self.assertEqual(FakeIngress.requests[2]["user"], f"telegram-{OWNER}-2")
        self.assertEqual(FakeIngress.requests[2]["messages"], [{"role": "user", "content": "Hello again"}])

    def test_a_question_card_becomes_numbered_choices_and_a_number_answers_it(self):
        self.turn_on()
        FakeIngress.answer, FakeIngress.questions = "", CARD
        self.poll(update(1, "I am vegetarian, remember that."))
        self.assertEqual(FakeTelegram.sent[-1][1], telegram.questions_text(CARD))
        FakeIngress.answer, FakeIngress.questions = "I will remember that.", None
        self.poll(update(2, "1"))
        self.assertEqual(FakeIngress.requests[-1]["messages"][-1]["content"], 'Remember this about you? "The owner is vegetarian."\nSave')
        self.assertEqual(FakeTelegram.sent[-1][1], "I will remember that.")
        self.poll(update(3, "1"))
        self.assertEqual(FakeIngress.requests[-1]["messages"][-1]["content"], "1", "the card was answered already")

    def test_commands_other_messages_and_portal_failures_get_a_plain_reply(self):
        self.turn_on()
        self.poll(update(1, "/start"))
        self.assertEqual(FakeTelegram.sent[-1][1], telegram.HELP)
        self.poll(update(2, None))
        self.assertEqual(FakeTelegram.sent[-1][1], telegram.TEXT_ONLY)
        FakeIngress.status = 503
        self.poll(update(3, "Hello?"))
        self.assertEqual(FakeTelegram.sent[-1][1], "Portal is busy; try again in a moment.")
        self.service.ingress = "/nonexistent/ingress.sock"
        self.poll(update(4, "Hello?"))
        self.assertEqual(FakeTelegram.sent[-1][1], "Portal is not running or did not answer.")
        self.assertEqual(len([item for item in FakeIngress.requests]), 1, "commands never reach Portal")

    def test_a_long_answer_arrives_in_pieces(self):
        self.turn_on()
        FakeIngress.answer = "word " * 2000
        self.poll(update(1, "Tell me everything."))
        self.assertGreater(len(FakeTelegram.sent), 2)
        self.assertTrue(all(len(text) <= telegram.MAX_MESSAGE for _, text in FakeTelegram.sent))

    def test_off_or_unset_reads_nothing_and_failures_show_once_in_settings(self):
        self.assertFalse(self.service.telegram_poll())
        self.owner("telegram-save", enabled=False, ownerId=OWNER, apiRoot=self.api, token=TOKEN)
        self.assertFalse(self.service.telegram_poll())
        self.assertEqual(FakeTelegram.offsets, [])
        self.turn_on()
        FakeTelegram.status = 409
        stop = threading.Event()
        waits = []
        stop.wait = lambda seconds: (waits.append(seconds), len(waits) >= 3 and stop.set())[1]
        telegram.telegram_loop(self.service, stop)
        view = self.owner("get")
        self.assertIn("Another program is reading this bot's messages", view["telegram"]["lastError"])
        self.assertEqual([entry["action"] for entry in view["activity"]].count("telegram"), 1, "told once, not every retry")
        self.assertEqual(waits, [telegram.RETRY_SECONDS] * 3)
        FakeTelegram.status = 200
        self.poll(update(1, "Back?"))
        self.assertEqual(self.owner("get")["telegram"]["lastError"], "")

    def test_a_failed_answer_leaves_the_messages_after_it_waiting(self):
        self.turn_on()
        FakeTelegram.refuse_sends = 1
        with self.assertRaises(IntegrationError):
            self.poll(update(1, "one"), update(2, "two"), update(3, "three"))
        # The first was handled and marked read; the two after it are read again next time.
        self.assertEqual(self.store.telegram()["offset"], 2)
        self.assertTrue(self.service.telegram_poll())
        self.assertEqual([request["messages"][0]["content"] for request in FakeIngress.requests], ["one", "two", "three"])
        self.assertEqual(len(FakeTelegram.sent), 2)
        self.assertEqual(self.store.telegram()["offset"], 4)
        # A later poll with nothing new clears a problem shown before.
        self.service._telegram_change(lambda current: {**current, "lastError": "Telegram could not be reached."})
        FakeTelegram.updates.clear()
        self.assertTrue(self.service.telegram_poll())
        self.assertEqual(self.store.telegram()["lastError"], "")

    def test_a_new_token_starts_reading_that_bot_from_scratch(self):
        self.turn_on()
        self.poll(update(41, "Hi"))
        self.assertEqual(self.store.telegram()["offset"], 42)
        self.owner("telegram-save", enabled=True, ownerId=OWNER, apiRoot=self.api, token="111111111:BBH" + "y" * 32)
        state = self.store.telegram()
        self.assertEqual((state["offset"], state["bot"]), (0, ""))


if __name__ == "__main__":
    unittest.main()
