import json
import os
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from project_transport import serve_project_connection


@unittest.skipUnless(sys.platform.startswith("linux") or sys.platform == "darwin", "local peer credentials")
class ProjectTransportTests(unittest.TestCase):
    def exchange(self, controller, envelope, owner=None):
        client, server = socket.socketpair()
        errors = []
        def serve():
            try:
                with server:
                    serve_project_connection(server, controller=controller,
                                             owner_uid=os.getuid() if owner is None else owner)
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=serve)
        client.sendall(json.dumps(envelope).encode() + b"\n")
        worker.start()
        with client:
            client.settimeout(5)
            received = bytearray()
            while b"\n" not in received:
                chunk = client.recv(4096)
                if not chunk:
                    break
                received.extend(chunk)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        return json.loads(received)

    def test_real_peer_identity_and_replay_binding(self):
        controller = Mock()
        controller.submit.return_value = {"id": "ods-project-" + "a" * 24, "state": "queued",
            "request": {"project": "project"}, "cancel_requested": False, "steps": [], "output": None}
        envelope = {"context": {"sessionId": "session-1", "toolCallId": "call-1"},
                    "request": {"schemaVersion": 1, "action": "submit", "project": "project", "outputDirectory": "out"}}
        self.assertEqual(self.exchange(controller, envelope)["status"], "queued")
        first = controller.submit.call_args.args[0]
        self.exchange(controller, envelope)
        self.assertEqual(controller.submit.call_args.args[0], first)
        envelope["context"]["sessionId"] = "session-2"
        self.exchange(controller, envelope)
        self.assertNotEqual(controller.submit.call_args.args[0], first)

    def test_wrong_peer_cannot_reach_controller(self):
        controller = Mock()
        result = self.exchange(controller, {}, owner=os.getuid() + 1)
        self.assertEqual(result["status"], "denied")
        self.assertEqual(controller.mock_calls, [])

    def test_policy_denial_is_preserved_without_leaking_exception(self):
        controller = Mock()
        controller.observe.side_effect = PermissionError("private detail")
        result = self.exchange(controller, {"context": {"sessionId": "session", "toolCallId": "call"},
            "request": {"schemaVersion": 1, "action": "observe", "jobId": "ods-project-" + "a" * 24}})
        self.assertEqual(result["status"], "denied")
        self.assertNotIn("private", json.dumps(result))

    def test_health_rechecks_runtime_and_does_not_claim_ready_after_docker_failure(self):
        controller = Mock(image='sha256:' + 'a' * 64)
        with patch('project_transport.verify_runtime', side_effect=OSError('offline')) as verify:
            result = self.exchange(controller, {'schemaVersion': 1, 'action': 'health'})
        self.assertEqual(result['status'], 'unconfirmed')
        verify.assert_called_once_with(controller.image)
        controller.submit.assert_not_called()

    def test_health_reports_only_configured_verified_profiles(self):
        image, python_image = 'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64
        controller = Mock(image=image, python_image=None)
        with patch('project_transport.verify_runtime'):
            legacy = self.exchange(controller, {'schemaVersion': 1, 'action': 'health'})
        self.assertEqual(legacy['status'], 'ready')
        self.assertNotIn('runtimes', legacy)
        controller.python_image = python_image
        with patch('project_transport.verify_runtime') as verify:
            result = self.exchange(controller, {'schemaVersion': 1, 'action': 'health'})
        self.assertEqual(result['runtimes'], {'npm': image, 'python': python_image})
        self.assertEqual(verify.call_count, 2)
        verify.assert_called_with(python_image, runtime='python')
        with patch('project_transport.verify_runtime', side_effect=[None, OSError('missing Python image')]):
            failed = self.exchange(controller, {'schemaVersion': 1, 'action': 'health'})
        self.assertEqual(failed['status'], 'unconfirmed')
        self.assertNotIn('runtimes', failed)
