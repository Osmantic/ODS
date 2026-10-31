"""Bounded public plans for durable goal turns. Model reports are not proof."""
import copy
import json
import re

# The Portal limits that stop a round without ending its goal (pixel_outcome
# `stop`, set by the Pixel plugin's tool-loop-guard). The next round continues
# from the saved work, within the same round and stall limits as any other.
# Pixel stops a round just before its run deadline even when an attempt was
# retried (run-deadline.mjs responseDeadlineDelay), ahead of the ingress's
# whole-response limit. A round that limit still cuts off (such as one in a
# long compaction between attempts) has no outcome and ends the goal, never
# replayed.
GOAL_CONTINUE_STOPS = frozenset({'tool-limit', 'run-deadline'})
# Plain words without defect vocabulary: Pixel picks its defect-report guide
# from this prompt (tool-loop-guard.mjs userMessageReportsDefect).
_STOP_NOTES = {
    'tool-limit': "The last round reached Portal's tool limit before it finished. ",
    'run-deadline': "The last round reached Portal's time limit for one response before it finished. ",
}
# Pixel marks a stopped round's plan blocked with its own stop summary. The
# goal card shows the saved plan until the next round publishes its own, so
# that plan is saved as still active, with this summary.
_STOPPED_SUMMARY = 'Continuing from the saved work'
# Stopped rounds in a row that may continue without a newly completed step.
GOAL_STOPPED_ROUNDS = 3


def public_plan(value):
    def text(v, limit):
        return isinstance(v, str) and 0 < len(v.strip()) <= len(v) <= limit and not re.search(r'[\x00-\x1f\x7f]', v)
    if not isinstance(value, dict) or set(value) != {'status', 'summary', 'steps'}:
        return None
    if value['status'] not in ('active', 'completed', 'blocked', 'waiting') or not text(value['summary'], 300):
        return None
    if not isinstance(value['steps'], list) or len(value['steps']) > 8:
        return None
    ids = set()
    for step in value['steps']:
        if not isinstance(step, dict) or set(step) != {'id', 'title', 'status'}:
            return None
        if not isinstance(step['id'], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', step['id']) or step['id'] in ids:
            return None
        if not text(step['title'], 160) or step['status'] not in ('pending', 'running', 'completed', 'blocked'):
            return None
        ids.add(step['id'])
    if value['status'] == 'completed' and (not ids or any(s['status'] != 'completed' for s in value['steps'])):
        return None
    return copy.deepcopy(value)


def goal_prompt(row, agent, answer=''):
    # Pixel's goal note points its language rule at the owner's words, and its
    # stop synthesis reads them: the goal before "Continue this goal from the
    # saved work." and the "Owner's answers:" (pixel-agent
    # plugin/response-language.mjs).
    plan = agent.get('goal_plan')
    answers = agent.get('goal_answers', '')
    if answer:
        answers = (answers + '\n\n' + answer).strip()
    completion_hint = ''
    if plan and plan['steps']:
        completed = {**plan, 'status':'completed', 'summary':'Requested outcome checked',
                     'steps':[{**step, 'status':'completed'} for step in plan['steps']]}
        completion_hint = ('\nReview the previous response against the objective now. Do missing work if necessary. '
                           'If the result already fulfills ALL requirements, do not repeat the work: '
                           'record completion via tool_call, id="pixel_ods_goal", args='
                           + json.dumps(completed, ensure_ascii=False) + '. '
                           'Use these completion arguments ONLY after checking the result. '
                           'Then deliver the checked result. Do not merely restate an answer while leaving the plan unfinished.\n')
    plan_text = ('\n'.join(f"{step['id']}: {step['title']} [{step['status']}]" for step in plan['steps']) if plan else '')
    # After "Continue this goal from the saved work.": Pixel reads the text
    # before that sentence as the owner's words.
    stop_note = _STOP_NOTES.get(agent.get('goal_stopped'), '')
    prompt = (f"/goal {row['goal']}\n\n"
            f"Continue this goal from the saved work. {stop_note}Do only unfinished work. "
            "Inspect uncertain effects before acting; never repeat a completed write or publication. "
            "Use pixel_ods_goal to record actual progress and completion; preserve existing step IDs and titles. "
            "When everything is done, report completed through that tool and deliver the actual final answer. "
            "Ask the owner with pixel_ods_ask_user when their input is necessary. "
            "All existing tool permissions still apply. Do not create other agents.\n"
            f"Conversation context (background only):\n{row['context'][:600]}\n"
            + (f"Saved public plan (model-reported):\n{plan_text}\n" if plan_text else '')
            + (f"Owner's answers:\n{answers}\n" if answers else ''))
    # The exact completion example is optional. Never trim the objective,
    # decisions or plan to make room for extra coaching on a small context.
    return prompt + completion_hint if len(prompt + completion_hint) <= 16384 else prompt


def round_file_edits(activity):
    """Completed file-changing calls of one round, from its public activity.
    An edit a stop cut off is still running, not completed."""
    activity = activity or {}
    for item in activity.get('activities') or []:
        if isinstance(item, dict) and item.get('kind') == 'edit':
            calls, failures = item.get('calls'), item.get('failures')
            if type(calls) is int and type(failures) is int and calls >= failures >= 0:
                running = sum(1 for event in activity.get('events') or [] if isinstance(event, dict)
                              and event.get('kind') == 'edit' and event.get('state') == 'running')
                return max(calls - failures - running, 0)
    return 0


def goal_decision(agent, stopped=None):
    """Called only after a successful, verified terminal transport receipt, or
    after a round that one of GOAL_CONTINUE_STOPS stopped (`stopped`)."""
    plan = public_plan((agent.get('activity') or {}).get('goal'))
    old = agent.get('goal_plan')
    if old and old['steps'] and plan and plan['steps'] and [(s['id'], s['title']) for s in old['steps']] != [(s['id'], s['title']) for s in plan['steps']]:
        return 'The model replaced its unfinished plan. Saved work was preserved; review it before continuing.'
    if plan and stopped and plan['status'] == 'blocked':
        plan = {**plan, 'status': 'active', 'summary': _STOPPED_SUMMARY}
    if plan and plan['steps']:
        agent['goal_plan'] = plan
    # A stopped round's completion report is checked by the next round.
    if plan and plan['status'] == 'completed' and not stopped:
        return None
    if plan and plan['status'] in {'blocked', 'waiting'}:
        return plan['summary']
    completed = sorted(s['id'] for s in (plan or {}).get('steps', []) if s['status'] == 'completed')
    stepped = completed != agent.get('goal_progress', [])
    # A round a Portal limit stopped can be in the middle of a long step, so
    # its saved file changes count as progress too, for at most
    # GOAL_STOPPED_ROUNDS such rounds in a row. Other rounds need a newly
    # completed step.
    agent['goal_stopped_rounds'] = agent.get('goal_stopped_rounds', 0) + 1 if stopped and not stepped else 0
    progressed = stepped or (bool(stopped) and round_file_edits(agent.get('activity')) > 0)
    agent['goal_stalls'] = 0 if progressed else agent.get('goal_stalls', 0) + 1
    agent['goal_progress'] = completed
    agent['goal_stopped'] = stopped
    if agent['goal_stalls'] >= 4 or agent['goal_stopped_rounds'] > GOAL_STOPPED_ROUNDS or agent.get('goal_round', 0) >= 11:
        return 'The goal is incomplete. Automatic continuation paused because progress could not be confirmed or the turn limit was reached.'
    return 'continue'
