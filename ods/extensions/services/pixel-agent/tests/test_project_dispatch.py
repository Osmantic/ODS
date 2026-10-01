from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from project_dispatch import dispatch_project


class ProjectDispatchTests(unittest.TestCase):
    def test_model_cannot_supply_replay_key_or_execution_options(self):
        controller = Mock()
        request = {"schemaVersion": 1, "action": "submit", "project": "project", "outputDirectory": "out"}
        for extra in ({"request_key": "a" * 64}, {"command": "anything"}, {"image": "anything"}):
            with self.assertRaises(ValueError):
                dispatch_project(controller, {**request, **extra}, request_key="a" * 64)
        with self.assertRaises(ValueError):
            dispatch_project(controller, request)
        controller.submit.assert_not_called()

    def test_dispatch_preserves_authorization_failure(self):
        controller = Mock()
        controller.observe.side_effect = PermissionError("denied")
        with self.assertRaises(PermissionError):
            dispatch_project(controller, {"schemaVersion": 1, "action": "observe", "jobId": "ods-project-" + "a" * 24})

    def test_submit_returns_only_public_job_state(self):
        controller = Mock()
        controller.submit.return_value = {"id": "ods-project-" + "a" * 24, "state": "queued",
            "request": {"project": "project"}, "cancel_requested": False, "steps": [], "output": None,
            "request_key": "private", "request_hash": "private"}
        result = dispatch_project(controller, {"schemaVersion": 1, "action": "submit", "project": "project",
                                              "outputDirectory": "out"}, request_key="b" * 64)
        controller.submit.assert_called_once_with("b" * 64, "project", "out")
        self.assertNotIn("request_key", result)
        self.assertNotIn("request_hash", result)
        self.assertEqual(result["status"], "queued")
