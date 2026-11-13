import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires Unix sockets; run under Linux/WSL")

import json
import pathlib
import stat
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "host"))
import integrations_memory as memory  # noqa: E402
import integrations_service as service  # noqa: E402
from integrations_common import IntegrationError, Store  # noqa: E402

CHAT = "ods-" + "a" * 64
OTHER = "ods-" + "b" * 64


def blank(**patch):
    return {"enabled": True, "askFirst": True, "items": [], "pending": [], "quietChats": [], **patch}


class PureMemory(unittest.TestCase):
    def test_a_fact_is_one_trimmed_line_of_reasonable_length(self):
        self.assertEqual(memory.fact("  The owner\n is   vegetarian. "), "The owner is vegetarian.")
        for value, reason in ((None, "as text"), ("   ", "as text"), (12, "as text"), ("x" * 241, "under 240")):
            with self.assertRaises(IntegrationError) as caught:
                memory.fact(value)
            self.assertIn(reason, caught.exception.message)
        # Control characters go (the chat card refuses them), and length is counted as the card counts it.
        self.assertEqual(memory.fact("The owner\x1b[31m is\x00 vegetarian.\x7f"), "The owner [31m is vegetarian.")
        self.assertEqual(memory.fact("🌱" * 120), "🌱" * 120)
        with self.assertRaises(IntegrationError):
            memory.fact("🌱" * 121)

    def test_only_portal_chat_sessions_count(self):
        self.assertEqual(memory.session(CHAT), CHAT)
        for value in ("", None, "ods-123", "agent:pixel:openai-user:" + CHAT, CHAT.upper(), CHAT + "x"):
            self.assertEqual(memory.session(value), "")

    def test_asking_first_keeps_the_fact_pending_once(self):
        state, reply = memory.propose(blank(), "The owner is vegetarian.", CHAT)
        self.assertEqual(reply["status"], "ask")
        self.assertEqual(state["items"], [])
        again, second = memory.propose(state, "the owner is vegetarian", CHAT)
        self.assertEqual(second["id"], reply["id"], "the same fact asked again waits once")
        self.assertEqual(len(again["pending"]), 1)

    def test_saving_without_asking_keeps_it_at_once_and_a_known_fact_is_not_added_twice(self):
        state, reply = memory.propose(blank(askFirst=False), "The owner's sister is Ana.", CHAT)
        self.assertEqual(reply["status"], "saved")
        self.assertEqual([item["text"] for item in state["items"]], ["The owner's sister is Ana."])
        self.assertEqual(state["items"][0]["chat"], CHAT)
        state, reply = memory.propose(state, "The owner's sister is Ana", OTHER)
        self.assertEqual(reply["status"], "known")
        self.assertEqual(len(state["items"]), 1)

    def test_memory_off_or_a_quiet_chat_refuses_to_learn(self):
        with self.assertRaises(IntegrationError) as caught:
            memory.propose(blank(enabled=False), "The owner likes tea.", CHAT)
        self.assertEqual(caught.exception.code, "memory-off")
        with self.assertRaises(IntegrationError) as caught:
            memory.propose(blank(quietChats=[CHAT]), "The owner likes tea.", CHAT)
        self.assertEqual(caught.exception.code, "memory-quiet-chat")
        # Another chat, or a scheduled task outside any chat, still learns.
        self.assertEqual(memory.propose(blank(quietChats=[CHAT]), "The owner likes tea.", OTHER)[1]["status"], "ask")
        self.assertEqual(memory.propose(blank(quietChats=[CHAT]), "The owner likes tea.", "")[1]["status"], "ask")

    def test_the_owner_s_answer_saves_drops_or_stops_asking(self):
        state, _ = memory.propose(blank(), "The owner is vegetarian.", CHAT)
        saved, outcome = memory.answer(state, "The owner is vegetarian.", "save", CHAT)
        self.assertEqual((outcome, [item["text"] for item in saved["items"]], saved["pending"], saved["askFirst"]),
                         ("saved", ["The owner is vegetarian."], [], True))
        dropped, outcome = memory.answer(state, "The owner is vegetarian.", "discard", CHAT)
        self.assertEqual((outcome, dropped["items"], dropped["pending"]), ("discarded", [], []))
        always, outcome = memory.answer(state, "The owner is vegetarian.", "always", CHAT)
        self.assertEqual((outcome, len(always["items"]), always["askFirst"]), ("saved", 1, False))
        self.assertEqual(memory.answer(saved, "The owner is vegetarian.", "save", CHAT)[1], "unknown", "an answer to nothing waiting changes nothing")
        with self.assertRaises(IntegrationError):
            memory.answer(state, "The owner is vegetarian.", "maybe", CHAT)
        # A card answers only the fact its own chat proposed.
        elsewhere, outcome = memory.answer(state, "The owner is vegetarian.", "save", OTHER)
        self.assertEqual((outcome, elsewhere["items"], len(elsewhere["pending"])), ("unknown", [], 1))

    def test_settings_decisions_edits_and_deletes(self):
        state, reply = memory.propose(blank(), "The owner runs on Sundays.", CHAT)
        state, _ = memory.decide(state, reply["id"], "save")
        item = state["items"][0]
        state = memory.edit(state, item["id"], " The owner runs on Saturdays. ")
        self.assertEqual(state["items"][0]["text"], "The owner runs on Saturdays.")
        state = memory.delete(state, item["id"])
        self.assertEqual(state["items"], [])
        for change in (lambda: memory.edit(state, "nope", "x"), lambda: memory.delete(state, "nope"),
                       lambda: memory.decide(state, "nope", "save")):
            with self.assertRaises(IntegrationError) as caught:
                change()
            self.assertEqual(caught.exception.code, "memory-missing")

    def test_marking_a_chat_quiet_drops_its_waiting_facts_and_can_be_undone(self):
        state, _ = memory.propose(blank(), "The owner has a dog.", CHAT)
        state, _ = memory.propose(state, "The owner has a cat.", OTHER)
        quiet = memory.quiet_chat(state, CHAT, False)
        self.assertEqual(quiet["quietChats"], [CHAT])
        self.assertEqual([item["text"] for item in quiet["pending"]], ["The owner has a cat."])
        self.assertEqual(memory.quiet_chat(quiet, CHAT, True)["quietChats"], [])
        self.assertEqual(memory.quiet_chat(memory.quiet_chat(quiet, CHAT, False), CHAT, False)["quietChats"], [CHAT])
        with self.assertRaises(IntegrationError):
            memory.quiet_chat(state, "chat-1", False)

    def test_each_turn_gets_the_newest_facts_within_the_cap_as_data(self):
        items = [{"id": str(n), "text": f"Fact number {n} " + "x" * 80, "createdAt": n, "updatedAt": n, "chat": ""} for n in range(60)]
        text = memory.context_text(blank(items=items), CHAT)
        self.assertTrue(text.startswith(memory.CONTEXT_HEAD))
        self.assertLessEqual(len(text), memory.MAX_CONTEXT)
        self.assertIn("Fact number 59", text)
        self.assertNotIn("Fact number 0 ", text)
        lines = text.splitlines()[1:]
        self.assertLess(text.index(lines[0]), text.index("Fact number 59"), "kept in the order they were learned")
        self.assertEqual(memory.context_text(blank(items=items, enabled=False), CHAT), "")
        self.assertEqual(memory.context_text(blank(items=items, quietChats=[CHAT]), CHAT), "")
        self.assertEqual(memory.context_text(blank(), CHAT), "")

    def test_bounds(self):
        state = blank(askFirst=False)
        for n in range(memory.MAX_ITEMS + 5):
            state, _ = memory.propose(state, f"Fact {n}", CHAT)
        self.assertEqual(len(state["items"]), memory.MAX_ITEMS)
        self.assertEqual(state["items"][-1]["text"], f"Fact {memory.MAX_ITEMS + 4}")
        state = blank()
        for n in range(memory.MAX_PENDING + 3):
            state, _ = memory.propose(state, f"Pending {n}", CHAT)
        self.assertEqual(len(state["pending"]), memory.MAX_PENDING)

    def test_the_owner_s_view_shows_newest_first_without_chats(self):
        state, _ = memory.propose(blank(askFirst=False), "First", CHAT)
        state, _ = memory.propose(state, "Second", CHAT)
        state, _ = memory.propose({**state, "askFirst": True}, "Waiting", CHAT)
        view = memory.public(state)
        self.assertEqual([item["text"] for item in view["items"]], ["Second", "First"])
        self.assertEqual([item["text"] for item in view["pending"]], ["Waiting"])
        self.assertNotIn("chat", json.dumps(view))
        self.assertNotIn("quietChats", view)


class ServiceMemory(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name) / "state"
        self.store = Store(self.root)
        self.service = service.Integrations(self.store)

    def owner(self, action, **fields):
        return self.service.handle("owner", {"schemaVersion": 1, "action": action, **fields})

    def agent(self, action, **fields):
        return self.service.handle("agent", {"schemaVersion": 1, "action": action, **fields})

    def test_a_proposed_fact_is_answered_in_the_chat_and_used_in_later_turns(self):
        self.assertEqual(self.owner("get")["memory"], {"enabled": True, "askFirst": True, "items": [], "pending": []})
        self.assertEqual(self.agent("memory-context", session=CHAT), {"text": "", "enabled": True, "quiet": False})
        asked = self.agent("memory-propose", text="The owner is vegetarian.", session=CHAT)
        self.assertEqual(asked["status"], "ask")
        self.assertEqual(self.owner("get")["memory"]["pending"][0]["text"], "The owner is vegetarian.")
        self.assertEqual(self.agent("memory-answer", text="The owner is vegetarian.", choice="save", session=CHAT), {"status": "saved"})
        self.assertIn("- The owner is vegetarian.", self.agent("memory-context", session=OTHER)["text"])
        stat_mode = stat.S_IMODE((self.root / "memory.json").stat().st_mode)
        self.assertEqual(stat_mode, 0o600)
        actions = [entry["action"] for entry in self.owner("get")["activity"]]
        self.assertEqual(actions[:2], ["memory-answer", "memory-propose"])
        # The activity log never holds the fact: it stays after the owner deletes the memory.
        self.assertNotIn("vegetarian", json.dumps(self.owner("get")["activity"]))

    def test_portal_cannot_change_memory_settings_or_decide_for_the_owner(self):
        for action in sorted(memory.OWNER_ACTIONS):
            with self.assertRaises(IntegrationError) as caught:
                self.agent(action, enabled=False, askFirst=False, id="x", text="x", decision="save", session=CHAT, remember=False)
            self.assertEqual(caught.exception.code, "forbidden", action)
        with self.assertRaises(IntegrationError):
            self.owner("memory-propose", text="x")

    def test_the_owner_manages_memory_from_settings(self):
        self.owner("memory-settings", enabled=True, askFirst=False)
        self.agent("memory-propose", text="The owner likes short answers.", session=CHAT)
        item = self.owner("get")["memory"]["items"][0]
        view = self.owner("memory-edit", id=item["id"], text="The owner likes very short answers.")
        self.assertEqual(view["memory"]["items"][0]["text"], "The owner likes very short answers.")
        self.assertEqual(self.owner("memory-delete", id=item["id"])["memory"]["items"], [])
        self.owner("memory-settings", enabled=True, askFirst=True)
        pending = self.agent("memory-propose", text="The owner's dog is Rex.", session=CHAT)
        self.assertEqual(self.owner("memory-decide", id=pending["id"], decision="save")["memory"]["items"][0]["text"], "The owner's dog is Rex.")
        self.assertEqual(self.owner("memory-clear")["memory"]["items"], [])
        self.owner("memory-settings", enabled=False, askFirst=True)
        self.assertEqual(self.agent("memory-context", session=CHAT)["enabled"], False)
        with self.assertRaises(IntegrationError):
            self.owner("memory-settings", enabled="yes", askFirst=True)

    def test_a_chat_marked_not_to_be_remembered(self):
        self.owner("memory-settings", enabled=True, askFirst=False)
        self.agent("memory-propose", text="The owner is a nurse.", session=CHAT)
        self.owner("memory-chat", session=CHAT, remember=False)
        self.assertEqual(self.agent("memory-context", session=CHAT), {"text": "", "enabled": True, "quiet": True})
        with self.assertRaises(IntegrationError) as caught:
            self.agent("memory-propose", text="The owner works nights.", session=CHAT)
        self.assertEqual(caught.exception.code, "memory-quiet-chat")
        self.assertIn("nurse", self.agent("memory-context", session=OTHER)["text"])
        self.owner("memory-chat", session=CHAT, remember=True)
        self.assertIn("nurse", self.agent("memory-context", session=CHAT)["text"])
        with self.assertRaises(IntegrationError):
            self.owner("memory-chat", session=CHAT, remember="no")

    def test_memory_survives_a_restart_of_the_service(self):
        self.owner("memory-settings", enabled=True, askFirst=False)
        self.agent("memory-propose", text="The owner lives in Porto.", session=CHAT)
        restarted = service.Integrations(Store(self.root))
        context = restarted.handle("agent", {"schemaVersion": 1, "action": "memory-context", "session": CHAT})
        self.assertIn("Porto", context["text"])


if __name__ == "__main__":
    unittest.main()
