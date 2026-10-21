"""Guided recovery reuses custody checks and never duplicates a model worker."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_resume',
    ROOT / 'installers/macos/lib/pixel-native-resume.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.fixture
def scenario(tmp_path):
    preparation = tmp_path / 'data/pixel-native/preparation'
    preparation.mkdir(parents=True)
    state = SimpleNamespace(events=[], recorded=False, running=False, choice=True,
        model='download-started', watch_status='complete', watch_exit=0, checks=True,
        error=None, mismatched=False, interrupted=False)
    expected = {'identity': 'same-retained-runtime'}

    def private_json(path):
        if path.name == 'selection-update.json':
            return {} if state.mismatched else expected
        return {'original': path.name}

    def recover(install, source, **kwargs):
        assert install == tmp_path
        assert source == ROOT
        state.events.append(('recover', kwargs))
        if state.error:
            raise ValueError(state.error)
        if kwargs.get('inspect_continuation'):
            selected = state.choice
            if selected is None and kwargs.get('opencode_choice'):
                selected = kwargs['opencode_choice'] == 'enabled'
            return dict(requiresChoice=['opencode'] if selected is None else [],
                        optionalSetup={'opencode': {'selected': selected}})
        assert kwargs == dict(restore_host_agent=True, restore_optional_tools=True,
                              resume_model=True, opencode_choice='enabled' if state.choice is not False else 'disabled')
        return dict(modelUpgrade={'status': state.model})

    def watch(_):
        state.events.append(('watch',))
        if not state.interrupted:
            state.running = False
        return state.watch_exit

    def readiness(*args, **kwargs):
        assert kwargs['include_services'] is True
        state.events.append(('readiness', kwargs))
        return {'checks': [{'name': 'selected-native-model', 'passed': state.checks}]}

    def browser(command, **kwargs):
        state.events.append(('browser', command))
        assert command == ['/usr/bin/open', 'http://127.0.0.1:4321/']
        return SimpleNamespace(returncode=0)

    def call(**kwargs):
        if state.recorded:
            (preparation / 'selection-update.json').touch()
        return module.continue_install(tmp_path, ROOT,
            recovery=SimpleNamespace(selection=lambda *args: expected, recover=recover),
            config=SimpleNamespace(private_json=private_json),
            progress=SimpleNamespace(worker_running=lambda _: state.running,
                dashboard_url=lambda _: 'http://127.0.0.1:4321/', watch=watch,
                progress=lambda _: {'status': state.watch_status}),
            readiness=SimpleNamespace(observe_apis=readiness), run=browser, **kwargs)
    state.call = call
    return state


def test_guided_recovery_runs_selected_setup_then_progress_and_live_readback(scenario, capsys):
    assert scenario.call() == 0
    assert [item[0] for item in scenario.events] == ['recover', 'recover', 'browser', 'watch', 'readiness']
    output = capsys.readouterr().out
    assert 'http://127.0.0.1:4321/' in output
    assert 'Live model, service and Portal checks passed' in output
    assert 'installation complete' not in output.lower()


def test_existing_worker_is_observed_without_repeating_recovery(scenario):
    scenario.recorded = scenario.running = True
    assert scenario.call(no_open=True) == 0
    assert [item[0] for item in scenario.events] == ['recover', 'watch', 'readiness']
    assert scenario.events[0][1]['inspect_continuation'] is True


def test_unrelated_worker_cannot_bypass_recovery_proof(scenario):
    scenario.running = True
    with pytest.raises(ValueError, match='native-model-upgrade-already-running'):
        scenario.call()
    assert len(scenario.events) == 1


def test_conflicting_selection_stops_before_setup(scenario):
    scenario.recorded = scenario.mismatched = True
    with pytest.raises(ValueError, match='native-recovery-selection-changed'):
        scenario.call()
    assert not scenario.events


def test_unknown_historical_choice_is_asked_before_any_mutation(scenario):
    scenario.choice = None
    assert scenario.call(ask=lambda: 'enabled', no_open=True) == 0
    assert [entry[1].get('inspect_continuation', False) for entry in scenario.events if entry[0] == 'recover'] == [True, True, False]


def test_noninteractive_unknown_choice_refuses_without_mutation(scenario):
    scenario.choice = None
    with pytest.raises(ValueError, match='retained-opencode-choice-required'):
        scenario.call(non_interactive=True)
    assert len(scenario.events) == 1


@pytest.mark.parametrize('flags', [dict(non_interactive=True), dict(no_watch=True, no_open=True)])
def test_background_handoff_reports_pending_without_final_health_claim(scenario, flags, capsys):
    assert scenario.call(**flags) == 0
    assert [item[0] for item in scenario.events] == ['recover', 'recover']
    output = capsys.readouterr().out
    assert 'continues in the background' in output
    assert 'checks passed' not in output


def test_interrupted_watch_does_not_report_completion(scenario, capsys):
    scenario.watch_status = 'downloading'
    assert scenario.call(no_open=True) == 0
    assert not any(item[0] == 'readiness' for item in scenario.events)
    assert 'Live model' not in capsys.readouterr().out


def test_interrupted_watch_with_old_complete_record_leaves_worker_alone(scenario):
    scenario.recorded = scenario.running = scenario.interrupted = True
    assert scenario.call(no_open=True) == 0
    assert [item[0] for item in scenario.events] == ['recover', 'watch']


def test_failed_download_does_not_get_success_readback(scenario):
    scenario.watch_status, scenario.watch_exit = 'failed', 1
    assert scenario.call(no_open=True) == 1
    assert not any(item[0] == 'readiness' for item in scenario.events)


@pytest.mark.parametrize('model', ['selected-model', 'cloud-model'])
def test_no_download_is_needed_for_selected_or_cloud_model(scenario, model):
    scenario.model = model
    assert scenario.call(no_open=True) == 0
    assert [item[0] for item in scenario.events] == ['recover', 'recover', 'readiness']


def test_live_runtime_failure_is_not_hidden_by_finished_worker(scenario, capsys):
    scenario.checks = False
    assert scenario.call(no_open=True) == 1
    assert 'Still needs attention: selected-native-model' in capsys.readouterr().out


def test_custody_failure_stops_before_browser_or_worker(scenario):
    scenario.error = 'retained-native-selection-mismatch'
    with pytest.raises(ValueError, match=scenario.error):
        scenario.call()
    assert len(scenario.events) == 1
