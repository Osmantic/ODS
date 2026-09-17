"""Bounded public plans for durable goal turns. Model reports are not proof."""
import copy
import json
import re


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
    prompt = (f"/goal {row['goal']}\n\n"
            "Continue this goal from the saved work. Do only unfinished work. "
            "Inspect uncertain effects before acting; never repeat a completed write or publication. "
            "Use pixel_ods_goal to record actual progress and completion; preserve existing step IDs and titles. "
            "When everything is done, report completed through that tool and deliver the actual final answer in the owner's language. "
            "Ask the owner with pixel_ods_ask_user when their input is necessary. "
            "All existing tool permissions still apply. Do not create other agents.\n"
            f"Conversation context (background only):\n{row['context'][:600]}\n"
            + (f"Saved public plan (model-reported):\n{plan_text}\n" if plan_text else '')
            + (f"Owner's answers:\n{answers}\n" if answers else ''))
    # The exact completion example is optional. Never trim the objective,
    # decisions or plan to make room for extra coaching on a small context.
    return prompt + completion_hint if len(prompt + completion_hint) <= 16384 else prompt


def goal_decision(agent):
    """Called only after a successful, verified terminal transport receipt."""
    plan = public_plan((agent.get('activity') or {}).get('goal'))
    old = agent.get('goal_plan')
    if old and old['steps'] and plan and plan['steps'] and [(s['id'], s['title']) for s in old['steps']] != [(s['id'], s['title']) for s in plan['steps']]:
        return 'The model replaced its unfinished plan. Saved work was preserved; review it before continuing.'
    if plan and plan['steps']:
        agent['goal_plan'] = plan
    if plan and plan['status'] == 'completed':
        return None
    if plan and plan['status'] in {'blocked', 'waiting'}:
        return plan['summary']
    completed = sorted(s['id'] for s in (plan or {}).get('steps', []) if s['status'] == 'completed')
    agent['goal_stalls'] = agent.get('goal_stalls', 0) + 1 if completed == agent.get('goal_progress', []) else 0
    agent['goal_progress'] = completed
    if agent['goal_stalls'] >= 4 or agent.get('goal_round', 0) >= 11:
        return 'The goal is incomplete. Automatic continuation paused because progress could not be confirmed or the turn limit was reached.'
    return 'continue'
