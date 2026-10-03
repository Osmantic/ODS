"""Snapshot download proofs: protocol boundaries and opt-in real Docker Chromium."""
import hashlib
import copy
import io
import json
import os
import subprocess
import threading
import time
import urllib.request
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_preview_inspection import bundle, broker, protocol, capsule, ScriptedBrowser


PDF = b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n'


def download_step(data=PDF, path='artifact.pdf'):
    return dict(action='download', locator={'selector': '#download'}, path=path,
                expectedBytes=len(data), expectedSha256=hashlib.sha256(data).hexdigest())


def download_bundle(html='<a id="download" href="artifact.pdf" download>Download</a>', data=PDF, path='artifact.pdf'):
    return bundle('', [download_step(data, path)], {'index.html': html.encode(), path: data})


def test_download_identity_is_checked_against_snapshot():
    value = download_bundle()
    protocol.validate_bundle(value)
    value['request']['steps'][0]['expectedSha256'] = '0' * 64
    with pytest.raises(protocol.Invalid, match='download snapshot mismatch'):
        protocol.validate_bundle(value)


@pytest.mark.parametrize('changes', [
    {'path': '../artifact.pdf'}, {'path': '/artifact.pdf'}, {'path': 'https://x/a.pdf'},
    {'path': 'artifact.pdf?x'}, {'path': 'artifact.pdf#x'}, {'path': 'a\\artifact.pdf'},
    {'path': 'artifact.js'}, {'expectedBytes': 0}, {'expectedBytes': True},
    {'expectedBytes': 4194305}, {'expectedSha256': 'A' * 64}, {'script': '1'},
])
def test_download_protocol_rejects_ambiguous_or_unbounded_requests(changes):
    request = download_bundle()['request']
    request['steps'][0].update(changes)
    with pytest.raises(protocol.Invalid):
        protocol.validate_request(request)


def test_only_one_final_download():
    request = download_bundle()['request']
    request['steps'].append(copy.deepcopy(request['steps'][0]))
    with pytest.raises(protocol.Invalid):
        protocol.validate_request(request)


def test_unavailable_download_scope_does_not_claim_capture():
    result = protocol.failure('unavailable', download_bundle()['request'])
    assert 'was captured' not in result['scope']
    assert 'A failed or unavailable receipt does not verify a download' in result['scope']


def test_missing_file_or_wrong_length_fails_before_browser():
    for change in ({'path': 'missing.pdf'}, {'expectedBytes': len(PDF) + 1}):
        value = download_bundle()
        value['request']['steps'][0].update(change)
        with pytest.raises(protocol.Invalid, match='download snapshot mismatch'):
            protocol.validate_bundle(value)


def test_old_image_fails_before_snapshot_or_browser():
    request = download_bundle()['request']
    config = {'ownerUid': os.getuid(), 'docker': '/usr/bin/docker', 'imageId': 'sha256:' + 'a' * 64, 'transport': 'local'}
    with patch.object(broker, 'bounded_process', return_value=b'') as run:
        result = broker.inspect_request(request, config)
    assert result['errorCode'] == 'unsupported_capability'
    assert run.call_count == 1
    assert 'org.osmantic.ods.inspection.download' in str(run.call_args)


def test_quota_is_separate_before_any_browser_callback():
    argv = broker.capsule_argv({'docker': '/usr/bin/docker', 'imageId': 'x', 'transport': 'local'}, 'fixture')
    assert '/downloads:rw,noexec,nosuid,nodev,size=4m,mode=0700,uid=65534,gid=65534' in argv
    assert '--network=none' in argv and '--read-only' in argv


def test_capsule_get_head_headers_match_real_publisher(tmp_path):
    import workspace_preview
    files = {'index.html': b'<button id="download">Inspect</button>', 'artifact.pdf': PDF,
             'artifact.zip': b'PK\x05\x06' + b'\0' * 18, 'README.MD': 'Olá'.encode()}
    value = bundle('', [{'action': 'click', 'locator': {'selector': '#download'}}], files)
    site = value['request']['siteId']
    (tmp_path / site).mkdir()
    for name, data in files.items():
        (tmp_path / site / name).write_bytes(data)
    publisher = workspace_preview.PreviewHTTPServer(('127.0.0.1', 0), tmp_path)
    publisher.internal_proxy = False
    thread = threading.Thread(target=publisher.serve_forever, daemon=True)
    thread.start()

    class CheckingBrowser(ScriptedBrowser):
        def goto(self, url, **kwargs):
            super().goto(url, **kwargs)
            origin = url.split('/__ods_inspection__.html')[0]
            for name, data in files.items():
                for method in ('GET', 'HEAD'):
                    target = f'/{site}/{name}'
                    public_request = urllib.request.Request(f'http://127.0.0.1:{publisher.server_port}{target}',
                        headers={'Host': f'{site}.localhost:{publisher.server_port}'}, method=method)
                    with urllib.request.urlopen(public_request) as public, urllib.request.urlopen(
                            urllib.request.Request(origin + target, method=method)) as private:
                        public_headers = {k.lower(): v for k, v in public.headers.items() if k.lower() != 'date'}
                        private_headers = {k.lower(): v for k, v in private.headers.items() if k.lower() != 'date'}
                        assert public_headers == private_headers
                        if name.endswith(('.pdf', '.zip')):
                            assert private_headers['content-disposition'] == f'attachment; filename="{name}"'
                            assert private_headers['content-type'] == 'application/octet-stream'
                        else:
                            assert 'content-disposition' not in private_headers
                        assert private.read() == (data if method == 'GET' else b'')
                        assert private_headers['x-preview-sha256'] == hashlib.sha256(data).hexdigest()
    browser = CheckingBrowser()
    browser.site = site
    try:
        result = capsule.run_browser(value, playwright_factory=browser)
        assert result['status'] == 'passed', result
    finally:
        publisher.shutdown()
        publisher.server_close()


@pytest.mark.skipif(not os.environ.get('ODS_INSPECTION_TEST_IMAGE'), reason='real Docker opt in')
class TestDockerDownloads:
    def inspect(self, value):
        config = {'docker': '/usr/bin/docker', 'imageId': os.environ['ODS_INSPECTION_TEST_IMAGE'],
                  'ownerUid': os.getuid(), 'transport': 'local', 'snapshotRoot': '/unused'}
        name = 'ods-download-test-' + os.urandom(8).hex()
        try:
            result = subprocess.run(broker.capsule_argv(config, name),
                                    input=protocol.canonical(value), capture_output=True, timeout=50)
        finally:
            # A killed Docker CLI does not remove a still-running capsule.
            subprocess.run([*broker.docker_prefix(config), 'rm', '-f', name], capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr.decode()[-2000:]
        return json.loads(result.stdout)

    def test_real_pdf_download_bytes(self):
        result = self.inspect(download_bundle())
        assert result['status'] == 'passed', result
        assert result['steps'][0]['download'] == {'bytes': len(PDF), 'sha256': hashlib.sha256(PDF).hexdigest(),
                                                  'completed': True, 'eventCount': 1, 'trustedClick': True}

    @pytest.mark.parametrize('href', ['artifact.pdf', 'artifact.pdf?v=1', '%61rtifact.pdf'])
    def test_ordinary_click_before_final_download_reports_bounded_policy_failure(self, href):
        value = download_bundle(f'<a id="download" href="{href}" download>Download</a>')
        locator = {'role': 'link', 'name': 'Download', 'exact': True}
        value['request']['steps'] = [
            {'action': 'assert-visible', 'locator': locator},
            {'action': 'assert-text', 'locator': locator, 'expectedText': 'Download'},
            {'action': 'click', 'locator': locator}, download_step(),
        ]
        started = time.monotonic()
        result = self.inspect(value)
        assert time.monotonic() - started < 15
        assert result['status'] == 'failed', result
        assert result['steps'][-1]['index'] == 2
        assert result['steps'][-1]['errorCode'] == 'unexpected_download', result
        assert result['blockedRequests'] == ['download'], result
        assert not any(step.get('download') for step in result['steps'])
        assert 'was captured' not in result['scope']

    def test_publisher_attachment_link_without_download_attribute(self):
        result = self.inspect(download_bundle('<a id="download" href="artifact.pdf">Download</a>'))
        assert result['status'] == 'passed', result
        assert result['steps'][0]['download']['sha256'] == hashlib.sha256(PDF).hexdigest()

    def test_timer_before_actionable_click_is_not_click_evidence(self):
        html = '''<button id="download">Download</button><a id="automatic" href="artifact.pdf" download hidden>File</a>
        <div id="cover" style="position:fixed;inset:0;z-index:100;background:white"></div>
        <script>
        window.__odsSnapshotDownloadClick=()=>{};
        setTimeout(()=>{window.__odsSnapshotDownloadClick('trusted-click');automatic.click()},600);
        setTimeout(()=>cover.remove(),1400);
        </script>'''
        result = self.inspect(download_bundle(html))
        assert result['status'] == 'failed', result
        assert not any(step.get('download') for step in result.get('steps', [])), result

    def test_private_binding_is_not_exposed_to_main_world(self):
        html = '''<button id="download" onclick="if(typeof window.__odsSnapshotDownloadClick==='undefined')document.querySelector('a').click()">Download</button>
        <a href="artifact.pdf" download hidden>File</a>
        <script>EventTarget.prototype.addEventListener=()=>{};</script>'''
        result = self.inspect(download_bundle(html))
        assert result['status'] == 'passed', result
        assert result['steps'][0]['download']['trustedClick'] is True

    def test_hidden_control_with_automatic_download_does_not_pass(self):
        html = '''<button id="download" hidden>Download</button><a href="artifact.pdf" download hidden>File</a>
        <script>setTimeout(()=>document.querySelector('a').click(),300);setTimeout(()=>document.querySelector('button').hidden=false,600)</script>'''
        result = self.inspect(download_bundle(html))
        assert result['status'] == 'failed', result
        assert not any(step.get('download') for step in result.get('steps', [])), result

    def test_zip_and_exact_four_mib_download(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('readme.txt', 'Real snapshot ZIP bytes')
        for data, path in ((stream.getvalue(), 'artifact.zip'), (PDF + b' ' * (4194304 - len(PDF)), 'artifact.pdf')):
            value = download_bundle(f'<a id="download" href="{path}" download="../../malicious.exe">Download</a>', data, path)
            result = self.inspect(value)
            assert result['status'] == 'passed', result
            assert result['steps'][0]['download']['bytes'] == len(data)
            assert result['steps'][0]['download']['sha256'] == hashlib.sha256(data).hexdigest()
            assert 'malicious' not in json.dumps(result)

    @pytest.mark.parametrize('handler', [
        "const a=document.createElement('a');a.href='data:application/pdf,wrong';a.download='x.pdf';a.click()",
        "const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['wrong']));a.download='x.pdf';a.click()",
        "location.href='https://example.invalid/artifact.pdf'",
        "window.open('artifact.pdf')",
        "setTimeout(()=>document.querySelector('a').click(),6000)",
        "fetch('artifact.pdf',{method:'HEAD'})",
    ])
    def test_non_snapshot_or_missing_download_never_passes(self, handler):
        value = download_bundle(f'<a href="artifact.pdf" download hidden>File</a><button id="download" onclick="{handler}">Download</button>')
        result = self.inspect(value)
        assert result['status'] == 'failed', result
        assert not any(step.get('download') for step in result.get('steps', [])), result

    def test_second_download_cancels_proof(self):
        value = download_bundle('<a href="artifact.pdf" download hidden>File</a><button id="download" onclick="document.querySelector(\'a\').click();setTimeout(()=>document.querySelector(\'a\').click(),80)">Download</button>')
        result = self.inspect(value)
        assert result['status'] == 'failed', result
        assert 'download' in result['blockedRequests'], result

    def test_automatic_download_before_step_does_not_count(self):
        value = download_bundle('<a href="artifact.pdf" download id="download">Download</a>'
                                '<script>document.querySelector("a").click()</script>')
        result = self.inspect(value)
        assert result['status'] == 'failed', result
        assert not any(step.get('download') for step in result.get('steps', [])), result

    def test_wrong_snapshot_file_is_not_a_download_pass(self):
        files = {'index.html': b'<a id="download" href="other.pdf" download>Download</a>',
                 'artifact.pdf': PDF, 'other.pdf': PDF}
        value = bundle('', [download_step()], files)
        result = self.inspect(value)
        assert result['status'] == 'failed', result
        assert not any(step.get('download') for step in result.get('steps', []))

    def test_download_tmpfs_enforces_quota_without_callbacks(self):
        config = {'docker': '/usr/bin/docker', 'imageId': os.environ['ODS_INSPECTION_TEST_IMAGE'],
                  'ownerUid': os.getuid(), 'transport': 'local'}
        argv = broker.capsule_argv(config, 'ods-download-quota-' + os.urandom(8).hex())
        code = """import errno,os
with open('/downloads/quota', 'wb', buffering=0) as f:
    f.write(b'x' * 4194304)
    try:
        f.write(b'x')
    except OSError as error:
        assert error.errno == errno.ENOSPC
    else:
        raise AssertionError('download quota not enforced')
assert os.stat('/downloads/quota').st_size == 4194304
print('quota enforced before browser callbacks')
"""
        result = subprocess.run(argv[:-1] + ['-c', code], capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr.decode()
        assert b'quota enforced' in result.stdout

    def test_regular_click_still_cancels_download(self):
        value = download_bundle()
        value['request']['steps'] = [{'action': 'click', 'locator': {'selector': '#download'}}]
        result = self.inspect(value)
        assert result['status'] == 'failed' and set(result['blockedRequests']) & {'download', 'navigation'}, result

    def test_broker_cancellation_removes_capsule(self, tmp_path, monkeypatch):
        value = download_bundle('<button id="download">No download</button>')
        site = tmp_path / value['request']['siteId']
        site.mkdir()
        import base64
        for entry in value['files']:
            path = site / entry['path']
            path.write_bytes(base64.b64decode(entry['base64']))
            path.chmod(0o400)
        config = {'docker': '/usr/bin/docker', 'imageId': os.environ['ODS_INSPECTION_TEST_IMAGE'],
                  'ownerUid': os.getuid(), 'transport': 'local', 'snapshotRoot': str(tmp_path)}
        cancelled = threading.Event()
        name = os.urandom(16).hex()
        monkeypatch.setattr(broker.uuid, 'uuid4', lambda: SimpleNamespace(hex=name))
        timer = threading.Timer(3, cancelled.set)
        timer.start()
        try:
            with pytest.raises(ValueError, match='cancelled'):
                broker.inspect_request(value['request'], config, cancelled)
        finally:
            timer.cancel()
        running = subprocess.check_output(['/usr/bin/docker', 'ps', '-aq', '--filter', 'name=ods-preview-inspection-' + name])
        assert running.strip() == b''

    def test_real_publisher_broker_and_capsule(self, tmp_path):
        import workspace_preview
        workspace, snapshots = tmp_path / 'workspace', tmp_path / 'snapshots'
        workspace.mkdir(mode=0o700)
        snapshots.mkdir(mode=0o700)
        site = workspace / 'site'
        site.mkdir(mode=0o700)
        (site / 'index.html').write_text('<a id="download" href="artifact.pdf" download>Download</a>')
        (site / 'artifact.pdf').write_bytes(PDF)
        published = workspace_preview.publish_snapshot(workspace, snapshots, 'site', os.getuid())
        request = dict(schemaVersion=1, action='inspect', siteId=published['siteId'], sha256=published['sha256'],
                       viewport={'width': 800, 'height': 600}, steps=[download_step()])
        config = {'docker': '/usr/bin/docker', 'imageId': os.environ['ODS_INSPECTION_TEST_IMAGE'],
                  'ownerUid': os.getuid(), 'transport': 'local', 'snapshotRoot': str(snapshots)}
        result = broker.inspect_request(request, config)
        assert result['status'] == 'passed', result
        assert result['steps'][0]['download']['sha256'] == hashlib.sha256(PDF).hexdigest()
        if os.environ.get('ODS_INSPECTION_RECEIPT_FILE'):
            from pathlib import Path
            Path(os.environ['ODS_INSPECTION_RECEIPT_FILE']).write_text(json.dumps({'request': request, 'receipt': result}))
