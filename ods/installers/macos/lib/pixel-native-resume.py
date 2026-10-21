"""Guide an owner through the existing, verified native Pixel recovery."""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent


def helper(name):
    spec = importlib.util.spec_from_file_location('resume_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def confirm_opencode():
    # A curl | bash install uses stdin for source, not answers. Missing historical
    # selection must never be guessed from a missing executable or a default.
    with open('/dev/tty', 'r+', encoding='utf-8') as terminal:
        terminal.write('This older installation did not save its OpenCode choice.\n'
                       'Did you select OpenCode in the installer? [y/n]: ')
        terminal.flush()
        answer = terminal.readline().strip().lower()
    if answer not in ('y', 'yes', 'n', 'no'):
        raise ValueError('retained-opencode-choice-required')
    return 'enabled' if answer in ('y', 'yes') else 'disabled'


def recorded_selection(install_dir, recovery, config):
    preparation = install_dir / 'data/pixel-native/preparation'
    expected = recovery.selection(config.private_json(preparation / 'preparation.json'),
                                  config.private_json(preparation / 'activation.json'))
    path = preparation / 'selection-update.json'
    if not os.path.lexists(path):
        return False
    if config.private_json(path) != expected:
        raise ValueError('native-recovery-selection-changed')
    # This establishes which recovery was recorded, not current service health.
    return True


def continue_install(install_dir, ods_source, *, recovery, config, progress, readiness,
                     opencode_choice=None, non_interactive=False, no_watch=False,
                     no_open=False, ask=confirm_opencode, run=subprocess.run):
    recorded = recorded_selection(install_dir, recovery, config)
    inspection = recovery.recover(install_dir, ods_source, inspect_continuation=True,
                                  opencode_choice=opencode_choice)
    if inspection['requiresChoice']:
        if non_interactive:
            raise ValueError('retained-opencode-choice-required')
        opencode_choice = ask()
        # Revalidate a confirmed choice through the same retained contract.
        inspection = recovery.recover(install_dir, ods_source, inspect_continuation=True,
                                      opencode_choice=opencode_choice)
    selected = inspection['optionalSetup']['opencode']['selected']
    if selected is None:
        raise ValueError('retained-opencode-choice-required')
    choice = 'enabled' if selected else 'disabled'
    running = progress.worker_running(install_dir)
    if running:
        if not recorded:
            raise ValueError('native-model-upgrade-already-running')
        print('A model worker for this recovered installation is already running. Following its progress.', flush=True)
        model_status = 'download-started'
    else:
        print('Verifying the retained Pixel activation and completing your selected setup...', flush=True)
        result = recovery.recover(install_dir, ods_source, restore_host_agent=True,
            restore_optional_tools=True, resume_model=True, opencode_choice=choice)
        model_status = result['modelUpgrade']['status']
        print('Pixel recovery and selected setup passed their checks.', flush=True)
    url = progress.dashboard_url(install_dir)
    print('Portal: ' + url, flush=True)
    print('Finish browser setup using your current installation, then check Models and send a message in Portal.', flush=True)
    if not no_open and not non_interactive:
        opened = run(['/usr/bin/open', url], check=False, timeout=15)
        if opened.returncode:
            print('The browser could not be opened automatically. Open the Portal address above.', flush=True)
    if model_status == 'download-started':
        print('Keep the Mac awake and Docker running while the full model downloads and activates.', flush=True)
        if no_watch or non_interactive:
            print('The model worker continues in the background. Use the recovery command again to follow it, or view progress on Models.', flush=True)
            return 0
        outcome = progress.watch(install_dir)
        if outcome:
            return outcome
        # Ctrl+C ends only the display. A stale/intermediate status is never
        # converted into a successful completion or a second downloader.
        if progress.worker_running(install_dir) or progress.progress(install_dir)['status'] != 'complete':
            return 0
    print('Checking the current model, selected services and Portal route...', flush=True)
    result = readiness.observe_apis(install_dir, opencode_choice=choice, include_services=True)
    failed = [item['name'] for item in result['checks'] if not item['passed']]
    if failed:
        print('Still needs attention: ' + ', '.join(failed) + '. The retained installation was preserved.', flush=True)
        return 1
    print('Live model, service and Portal checks passed. Confirm a chat response and your selected features in the browser.', flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--ods-source', default=str(HERE.parents[2]))
    parser.add_argument('--opencode-choice', choices=('enabled', 'disabled'))
    parser.add_argument('--non-interactive', action='store_true')
    parser.add_argument('--no-watch', action='store_true')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'darwin' or os.geteuid() == 0:
        parser.error('Run as the signed-in macOS owner; recovery requests sudo when needed.')
    recovery = helper('pixel-native-recover')
    try:
        install_dir = Path(args.install_dir).expanduser().resolve(strict=True)
        source = Path(args.ods_source).expanduser().resolve(strict=True)
        relaunched = helper('pixel-native-recovery-python').relaunch(
            install_dir, HERE / 'pixel-native-resume.py', sys.argv[1:])
        if relaunched is not None:
            return relaunched
        return continue_install(install_dir, source, recovery=recovery,
            config=helper('pixel-native-config'), progress=helper('pixel-native-progress'),
            readiness=helper('pixel-native-readiness'), opencode_choice=args.opencode_choice,
            non_interactive=args.non_interactive, no_watch=args.no_watch, no_open=args.no_open)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print('Recovery stopped. ' + recovery.failure_detail(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nRecovery interrupted. Keep the retained installation; rerun the same recovery command to inspect or continue.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
