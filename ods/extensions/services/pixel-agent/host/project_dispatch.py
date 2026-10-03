"""Candidate request boundary for the project controller.

The authenticated transport supplies the replay key; a model cannot select it.
Authorization remains mandatory in the controller for every operation.
This module does not open a listener or grant execution permissions.
"""
import re


def dispatch_project(controller, request, *, request_key=None):
    if not isinstance(request, dict) or request.get("schemaVersion") != 1:
        raise ValueError("invalid project request")
    action = request.get("action")
    if action == 'capabilities':
        if set(request) != {'schemaVersion', 'action', 'runtime'} or request['runtime'] != 'python':
            raise ValueError('invalid runtime capability request')
        return controller.capabilities(request['runtime'])
    if action == 'diagnose':
        if set(request) != {'schemaVersion', 'action', 'runtime'} or not isinstance(request['runtime'], str):
            raise ValueError('invalid diagnostic request')
        if not isinstance(request_key, str) or not re.fullmatch(r'[a-f0-9]{64}', request_key):
            raise ValueError('authenticated replay key required')
        if request['runtime'] not in ('npm', 'python'):
            return {'schemaVersion': 1, 'kind': 'ods-project-diagnostic', 'scope': 'managed-executor',
                    'code': 'unsupported', 'supportedRuntimes': ['npm', 'python']}
        row = controller.diagnose(request_key, request['runtime'])
        if row.get('kind') == 'ods-project-diagnostic':
            return row
    elif action == "submit":
        if set(request) != {"schemaVersion", "action", "project", "outputDirectory"}:
            raise ValueError("invalid project submission")
        if not isinstance(request_key, str) or not re.fullmatch(r"[a-f0-9]{64}", request_key):
            raise ValueError("authenticated replay key required")
        row = controller.submit(request_key, request["project"], request["outputDirectory"])
    elif action in ("observe", "cancel"):
        if (set(request) != {"schemaVersion", "action", "jobId"}
                or not isinstance(request["jobId"], str)
                or not re.fullmatch(r"ods-project-[a-f0-9]{24}", request["jobId"])):
            raise ValueError("invalid project observation")
        row = getattr(controller, action)(request["jobId"])
    else:
        raise ValueError("unsupported project action")
    # Internal replay keys/database metadata are not tool output or authority.
    result = {"schemaVersion": 1, "kind": "ods-project-job", "jobId": row["id"],
            "status": row["state"], "project": row["request"]["project"],
            "cancelRequested": row["cancel_requested"], "steps": row["steps"],
            "output": row["output"]}
    if row['request'].get('kind') == 'diagnostic':
        result.update(purpose='diagnostic', scope='managed-executor', project=None,
                      runtime=row['request']['runtime'])
    return result
