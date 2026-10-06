"""Laya HTTP service with real, same-process inference qualification at startup."""
import math
import os
from pathlib import Path
import re

QUESTIONS = {
    'department': {'type': 'choice', 'instructions': 'Which department handles this request?',
                   'criteria': {'billing': 'payments and refunds', 'technical': 'software bugs', 'other': 'other topics'}},
    'urgency': {'type': 'score', 'instructions': 'How urgent is this request?',
                'criteria': ['low', 'medium', 'high']},
    'refund': {'type': 'noul', 'instructions': 'Is the customer asking for a refund?'},
}
STARTUP_CASES = [
    ('english', 'en', 'I was charged twice. Please refund the duplicate charge.'),
    ('multilingual', 'pt', 'Fui cobrado duas vezes. Quero o reembolso da cobranca duplicada.'),
    ('typed-decisions', 'en', 'A customer requests a refund for a duplicate charge. Route this ticket.'),
]


def probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def distribution(value, keys):
    return (isinstance(value, dict) and set(value) == set(keys)
            and all(probability(item) for item in value.values())
            and abs(sum(value.values()) - 1) <= 0.002)


def validate_startup_result(result, checkpoint):
    if not isinstance(result, dict) or result.get('routing', {}).get('model') != checkpoint:
        raise ValueError('Laya startup returned the wrong checkpoint')
    usage = result.get('usage', {})
    if (usage.get('truncated') is not False or usage.get('state_tokens_dropped') != 0
            or usage.get('truncated_questions') != []):
        raise ValueError('Laya startup did not prove complete context')
    answers = result.get('answers', {})
    if set(answers) != set(QUESTIONS):
        raise ValueError('Laya startup returned incomplete answers')
    choice, score, yesno = (answers[key] for key in QUESTIONS)
    if (choice.get('type') != 'choice' or choice.get('choice') not in QUESTIONS['department']['criteria']
            or not distribution(choice.get('probabilities'), QUESTIONS['department']['criteria'])
            or score.get('type') != 'score' or type(score.get('score')) not in (int, float)
            or not 0 <= score['score'] <= 2
            or score.get('legend') != {'0': 'low', '1': 'medium', '2': 'high'}
            or not distribution(score.get('probabilities'), ['0', '1', '2'])
            or yesno.get('type') != 'noul' or not probability(yesno.get('noul'))):
        raise ValueError('Laya startup returned invalid typed decisions')


def read_key(path):
    value = Path(path).read_text(encoding='ascii').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', value):
        raise ValueError('Invalid managed Laya API key')
    return value


def create_portal_app(*, router=None, key_file='/run/ods-laya/api-key'):
    from laya.serve import build_router, create_app
    os.environ['LAYA_API_KEY'] = read_key(key_file)
    if router is None:
        router = build_router()
    for checkpoint, language, text in STARTUP_CASES:
        result = router.predict(text, QUESTIONS, model=checkpoint, lang=language)
        validate_startup_result(result, checkpoint)
    app = create_app(router)

    @app.get('/ready')
    def ready():
        # No weights, device names, requests or credentials in the public probe.
        # This is startup inference evidence, not a quality/accuracy guarantee.
        return {'status': 'ok', 'startupInferenceVerified': True}

    return app


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(create_portal_app(), host='0.0.0.0', port=8000, access_log=False)
