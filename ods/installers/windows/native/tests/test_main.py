"""Entry point: config errors surface before any side effect; off Windows the Job Object is required."""
import json
import os
import sys

import pytest

from supervisor.__main__ import main
from supervisor.plan import ConfigError
from supervisor.runner import JobObjectUnavailable, log


def write_config(tmp_path, **overrides):
    config = {
        "schema_version": 1, "install_root": str(tmp_path / "app"), "state_root": str(tmp_path / "state"),
        "services": {"dashboard-api": {"port": 13002}},
    }
    config.update(overrides)
    path = tmp_path / "supervisor-config.json"
    path.write_text(json.dumps(config))
    return str(path)


def test_invalid_config_raises_before_creating_state(tmp_path):
    with pytest.raises(ConfigError, match="127.0.0.1"):
        main(["--config", write_config(tmp_path, host="0.0.0.0")])
    assert not (tmp_path / "state").exists()


@pytest.mark.skipif(os.name == "nt", reason="on Windows main() would start supervising")
def test_main_requires_job_objects(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # restored after the test
    handlers = list(log.handlers)
    try:
        with pytest.raises(JobObjectUnavailable):
            main(["--config", write_config(tmp_path)])
    finally:
        for handler in log.handlers[len(handlers):]:
            handler.close()
        log.handlers[:] = handlers
    assert (tmp_path / "state" / "logs" / "supervisor.log").read_text().count("supervisor starting") == 1
    assert (tmp_path / "state" / "run").is_dir()
