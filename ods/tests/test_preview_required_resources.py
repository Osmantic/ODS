"""Requested CSS/module failures cannot hide behind a passing heading check."""
import os
from types import SimpleNamespace
import unittest
from test_preview_inspection import bundle, capsule


class ResourceEvidenceTests(unittest.TestCase):
    def test_bounded_sanitized_and_relevant_evidence(self):
        errors = capsule.RequiredResources()
        for kind in ('image', 'font', 'other'):
            errors.failed(SimpleNamespace(resource_type=kind, url='http://local/favicon.ico'))
        self.assertEqual(errors.receipt(), {})
        for index in range(40):
            errors.failed(SimpleNamespace(resource_type='script', url=f'http://secret@local/chunk{index}.js?token=secret'))
        self.assertEqual(len(errors.errors), 16)
        self.assertNotIn('secret', str(errors.receipt()))

    def test_status_and_mime_failures(self):
        for kind, mime in [('stylesheet', 'text/html'), ('script', 'text/plain')]:
            with self.subTest(kind=kind):
                errors = capsule.RequiredResources()
                request = SimpleNamespace(resource_type=kind, url='http://local/file')
                errors.response(SimpleNamespace(request=request, status=200, headers={'content-type':mime}))
                self.assertEqual(errors.errors[0]['reason'], 'mime')
                errors.response(SimpleNamespace(request=request, status=404, headers={}))
                self.assertEqual(errors.errors[1]['reason'], 'http')


@unittest.skipUnless(os.environ.get('ODS_PREVIEW_BROWSER_TESTS') == '1', 'real Chromium opt in')
class ResourceBrowserTests(unittest.TestCase):
    def inspect(self, html, files=None):
        return capsule.run_browser(bundle(html, [{"action":"assert-text", "locator":{"selector":"h1"}, "expectedText":"Arcade"}], files))

    def test_missing_next_and_vite_assets_fail_even_with_visible_heading(self):
        for markup in ('<link rel="stylesheet" href="_next/static/css/missing.css">',
                       '<link rel="stylesheet" href="assets/missing.css">',
                       '<script type="module" src="assets/missing.js"></script>'):
            with self.subTest(markup=markup):
                result = self.inspect(markup+'<h1>Arcade</h1>')
                self.assertEqual(result['steps'][0]['status'], 'passed')
                self.assertEqual(result['status'], 'failed')
                self.assertTrue(any(v['reason'] in ('http', 'request_failed') for v in result['resourceErrors']))

    def test_wrong_mime_style_and_module_fail(self):
        for tag in ('<link rel="stylesheet" href="wrong.html">', '<script type="module" src="wrong.html"></script>'):
            with self.subTest(tag=tag):
                result = self.inspect('', {'index.html':(tag+'<h1>Arcade</h1>').encode(), 'wrong.html':b'<p>not an asset</p>'})
                self.assertEqual(result['status'], 'failed')
                self.assertTrue(any(v['reason'] in ('mime', 'request_failed') for v in result['resourceErrors']))

    def test_html_only_and_valid_root_assets_remain_supported(self):
        self.assertEqual(self.inspect('<h1>Arcade</h1>')['status'], 'passed')
        for root in ('_next/static', 'assets'):
            with self.subTest(root=root):
                html=f'<link rel="stylesheet" href="/{root}/app.css"><script type="module" src="/{root}/app.js"></script><h1>Loading</h1>'
                result=self.inspect('', {'index.html':html.encode(), f'{root}/app.css':b'h1{color:rgb(12,34,56)}', f'{root}/app.js':b'if(getComputedStyle(document.querySelector("h1")).color === "rgb(12, 34, 56)") document.querySelector("h1").textContent="Arcade";'})
                self.assertEqual(result['status'], 'passed')
                self.assertNotIn('resourceErrors', result)


if __name__ == '__main__':
    unittest.main()
