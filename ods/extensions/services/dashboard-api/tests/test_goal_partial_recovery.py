"""Goal mode's controller and the 'unfinished-work' stop.

The actual TeamManager and TeamStore drive synthetic Pixel frames: no model,
gateway or real history. A failed round that Pixel marks 'unfinished-work'
(saved, settled edits; requested publication not done) continues from the
saved work with a fresh durable request ID, inside the same shared stopped-
round, stall, plan-identity and turn limits as the other closed stops. Every
other failed, empty, errored or unconfirmed round still ends the goal.
Standard library only: python3 -m unittest discover -s tests -p 'test_goal_partial_recovery.py'.
"""
import asyncio
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pixel_agent_teams import TeamManager, TeamStore  # noqa: E402
from portal_goal import GOAL_CONTINUE_STOPS, GOAL_STOPPED_ROUNDS  # noqa: E402

OWNER = 'a' * 64
GOAL = 'Repair the appointment form in site/index.html and publish the site.'
PLAN = {'status': 'active', 'summary': 'Working', 'steps': [
    {'id': 'work', 'title': 'Repair the form', 'status': 'running'},
    {'id': 'publish', 'title': 'Publish the site', 'status': 'pending'}]}


def completed_plan():
    done = copy.deepcopy(PLAN)
    done['status'], done['summary'] = 'completed', 'Work checked and completed'
    for step in done['steps']:
        step['status'] = 'completed'
    return done


def frames(outcome, *, content='Saved the styles; the handler and publication remain.', edits=1, goal=None,
           done=True, error=False, stop=None):
    """One Pixel round as the private ingress streams it."""
    task = {'schemaVersion': 2, 'state': 'finished', 'calls': edits + 1, 'failures': 0, 'blocked': 0,
            'activities': [{'kind': 'read', 'calls': 1, 'failures': 0, 'blocked': 0}]
            + ([{'kind': 'edit', 'calls': edits, 'failures': 0, 'blocked': 0}] if edits else []),
            'goal': goal or copy.deepcopy(PLAN)}
    yield {'pixel_task': task}
    if error:
        yield {'error': {'message': 'upstream failed'}}
    if content:
        yield {'choices': [{'delta': {'content': content}}]}
    receipt = {'schemaVersion': 1, 'status': outcome}
    if stop is not None:
        receipt['stop'] = stop
    yield {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'pixel_outcome': receipt}
    if done:
        yield {'_done': True, '_state': 'complete'}


class GoalPartialRecovery(unittest.TestCase):
    def drive(self, rounds):
        """Run a goal whose Nth model turn yields rounds[N](); returns the row,
        each turn's request ID and the user messages the controller sent."""
        calls, prompts = [], []

        async def run(_owner, agent):
            calls.append(agent['request_id'])
            prompts.append(next((m['content'] for m in reversed(agent['messages']) if m['role'] == 'user'), ''))
            index = len(calls) - 1
            for frame in (rounds[index] if index < len(rounds) else rounds[-1])():
                yield frame

        async def cancel(*_):
            return True

        async def main():
            with tempfile.TemporaryDirectory(prefix='goal-partial-') as folder:
                manager = TeamManager(TeamStore(Path(folder)), run, cancel)
                manager.start(OWNER, 'chat', 'goal-request', GOAL, None, '', 'goal')
                for _ in range(40):
                    tasks = list(manager.tasks.values())
                    if not tasks:
                        break
                    await asyncio.gather(*tasks)
                    await asyncio.sleep(0)
                self.assertFalse(manager.tasks, 'no live controller task remains')
                return manager.list(OWNER, 'chat')[0]

        return asyncio.run(main()), calls, prompts

    def test_the_stop_is_a_closed_continue_stop(self):
        self.assertIn('unfinished-work', GOAL_CONTINUE_STOPS)
        self.assertEqual(GOAL_CONTINUE_STOPS, frozenset({'tool-limit', 'run-deadline', 'unfinished-work'}))

    def test_an_unfinished_round_continues_once_from_saved_work_with_a_fresh_request_id(self):
        row, calls, prompts = self.drive([
            lambda: frames('failed', stop='unfinished-work'),
            lambda: frames('none', content='Repaired and published; checked.', goal=completed_plan())])
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(set(calls)), 2, 'every round has its own durable request ID')
        follow_up = prompts[1]
        self.assertTrue(follow_up.startswith(f'/goal {GOAL}'), 'the original objective is kept')
        self.assertIn('Continue this goal from the saved work.', follow_up)
        self.assertIn('Inspect uncertain effects before acting; never repeat a completed write or publication.', follow_up)
        self.assertIn('All existing tool permissions still apply.', follow_up)
        self.assertIn('before the requested changes were finished', follow_up)

    def test_other_failed_empty_errored_or_unconfirmed_rounds_still_end_the_goal(self):
        for name, factory in [
            ('failed without a stop', lambda: frames('failed')),
            ('an unknown stop', lambda: frames('failed', stop='unfinished')),
            ('an empty reply', lambda: frames('failed', stop='unfinished-work', content='')),
            ('a transport error', lambda: frames('failed', stop='unfinished-work', error=True)),
            ('an unconfirmed end', lambda: frames('failed', stop='unfinished-work', done=False)),
        ]:
            with self.subTest(name):
                row, calls, _ = self.drive([factory, lambda: frames('none', goal=completed_plan())])
                self.assertEqual(len(calls), 1, name)
                self.assertNotEqual(row['status'], 'completed', name)

    def test_stopped_rounds_share_the_existing_limit(self):
        row, calls, _ = self.drive([
            lambda: frames('failed', stop='unfinished-work'),
            lambda: frames('failed', stop='tool-limit'),
            lambda: frames('failed', stop='unfinished-work'),
            lambda: frames('failed', stop='unfinished-work')])
        self.assertEqual(len(calls), GOAL_STOPPED_ROUNDS + 1)
        self.assertEqual(row['status'], 'failed')
        self.assertIn('Automatic continuation paused', row['agents'][0]['error'])

    def test_rounds_without_saved_edits_stall_out(self):
        row, calls, _ = self.drive([lambda: frames('failed', stop='unfinished-work', edits=0)])
        self.assertLessEqual(len(calls), GOAL_STOPPED_ROUNDS + 1)
        self.assertEqual(row['status'], 'failed')
        self.assertIn('Automatic continuation paused', row['agents'][0]['error'])

    def test_a_replaced_plan_ends_the_goal(self):
        replaced = copy.deepcopy(PLAN)
        replaced['steps'][0]['title'] = 'Rewrite everything'
        row, calls, _ = self.drive([
            lambda: frames('failed', stop='unfinished-work'),
            lambda: frames('failed', stop='unfinished-work', goal=replaced)])
        self.assertEqual(len(calls), 2)
        self.assertEqual(row['status'], 'failed')
        self.assertIn('replaced its unfinished plan', row['agents'][0]['error'])


if __name__ == '__main__':
    os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    unittest.main()
