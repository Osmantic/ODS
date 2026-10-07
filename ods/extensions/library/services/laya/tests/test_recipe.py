"""Validate the recipe with ODS's real schema, policy and literal env reader."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Optional
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import jsonschema
import yaml

SERVICE = Path(__file__).resolve().parents[1]
ODS = SERVICE.parents[3]
spec = importlib.util.spec_from_file_location('laya_setup', SERVICE / 'setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class RecipeTest(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Actual ODS resolver runs in WSL on Windows')
    def test_actual_resolver_honors_cpu_opt_out_and_nvidia_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = root / 'data/user-extensions/laya'
            recipe.parent.mkdir(parents=True)
            shutil.copytree(SERVICE, recipe, ignore=shutil.ignore_patterns('__pycache__'))
            (root / 'docker-compose.base.yml').write_text('services: {}\n')
            (root / 'docker-compose.nvidia.yml').write_text('services: {}\n')
            for mode, gpu in [('auto', True), ('cpu', False), ('cuda', True)]:
                (root / '.env').write_text('LAYA_ACCELERATION=' + mode + '\n')
                environment = {key: value for key, value in os.environ.items()
                               if key not in {'LAYA_ACCELERATION', 'ODS_SKIP_GPU_OVERLAYS',
                                              'ODS_SKIP_GPU_OVERLAYS_FOR', 'ODS_MODE'}}
                run = subprocess.run(['bash', str(ODS / 'scripts/resolve-compose-stack.sh'),
                                      '--script-dir', str(root), '--gpu-backend', 'nvidia'],
                                     env=environment, capture_output=True, text=True, check=True)
                self.assertIn('compose.yaml', run.stdout)
                self.assertEqual('compose.nvidia.yaml' in run.stdout, gpu, run.stdout + run.stderr)

    def test_setup_binds_the_actual_installed_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'ods'
            entry = root / 'extensions/services/pixel-agent/plugin/laya-setup-cli.mjs'
            entry.parent.mkdir(parents=True)
            entry.write_text('')
            recipe = Path(directory) / 'custom-data/user-extensions/laya/setup.py'
            recipe.parent.mkdir(parents=True)
            recipe.write_text('')
            with patch.object(setup, '__file__', str(recipe)), \
                    patch.object(setup.sys, 'argv', ['setup.py', str(root)]), \
                    patch.object(setup.shutil, 'which', return_value='/usr/bin/node'), \
                    patch.object(setup, 'configured_port', return_value='18017'), \
                    patch.object(setup.subprocess, 'run') as run:
                setup.main()
            run.assert_called_once_with(['/usr/bin/node', str(entry.resolve()),
                str(root.resolve()), '18017', str(recipe.resolve().parent / 'compose.yaml')], check=True)

    def test_manifest_and_actual_extension_scanner(self):
        manifest = yaml.safe_load((SERVICE / 'manifest.yaml').read_text())
        schema = json.loads((ODS / 'extensions/schema/service-manifest.v1.json').read_text())
        jsonschema.validate(manifest, schema)
        source = (ODS / 'extensions/services/dashboard-api/routers/extensions.py').read_text(encoding='utf-8')
        start = source.index('_LOOPBACK_VAR_DEFAULT_RE = re.compile(')
        end = source.index('\ndef ', source.index('def _scan_compose_content(') + 5)
        namespace = {'re': re, 'os': os, 'json': json, 'Path': Path,
                     'Optional': Optional, 'HTTPException': HTTPException, 'yaml': yaml,
                     'CORE_SERVICE_IDS': {'dashboard', 'dashboard-api', 'llama-server', 'open-webui'}}
        exec(compile(source[start:end], 'ODS extension compose scanner', 'exec'), namespace)
        namespace['_scan_compose_content'](SERVICE / 'compose.yaml', trusted=True, extension_id='laya')
        namespace['_scan_compose_content'](SERVICE / 'compose.nvidia.yaml', trusted=True,
                                          extension_id='laya', accelerator='nvidia')

    def test_port_uses_the_same_literal_grammar_as_ods(self):
        # Import the existing ODS parser; the fixture contains only owner config.
        setup.configured_port(ODS)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(setup.configured_port(root), '8017')
            for value in ['18017', "'18017' # local", '"18017"', '18017 # local']:
                (root / '.env').write_text('OTHER=ignored\nLAYA_PORT=' + value, encoding='utf-8')
                self.assertEqual(setup.configured_port(root), '18017')
            for value in ['0', '65536', '$(touch bad)', '${PORT}', '１２３４', '1.5', '-1']:
                (root / '.env').write_text('LAYA_PORT=' + value, encoding='utf-8')
                with self.subTest(value=value), self.assertRaises(ValueError):
                    setup.configured_port(root)


if __name__ == '__main__':
    unittest.main()
