from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_reenable_cleans_dead_container_before_compose_up():
    source = (ROOT / "bin/ods-host-agent.py").read_text(encoding="utf-8")
    action = source[source.index("def docker_compose_action"):source.index("def _proxy_compose_enabled")]
    assert "_remove_dead_service_container(service_id, flags)" in action
    assert 'in {"dead", "removing"}' in action
    assert '["docker", "rm", "-f", container_id]' in action
    assert action.index("_remove_dead_service_container") < action.index(
        '["docker", "compose"] + flags + ["up", "-d", service_id]'
    )
