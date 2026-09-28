#!/usr/bin/env python3
"""A global LAN preference must not publish private backend/extension ports."""
from pathlib import Path
import unittest

import yaml


ODS = Path(__file__).resolve().parents[2]


class ComposeLoader(yaml.SafeLoader):
    pass


def compose_tag(loader, suffix, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


ComposeLoader.add_multi_constructor('!', compose_tag)


class PrivatePortsTests(unittest.TestCase):
    def test_every_published_backend_port_is_literal_loopback(self):
        # These are authenticated entrypoints. No community service can gain
        # an exception by using the same service name in another file.
        entrypoints = {
            ('docker-compose.base.yml', 'dashboard'),
            ('docker-compose.base.yml', 'open-webui'),
            ('extensions/services/ods-proxy/compose.yaml', 'ods-proxy'),
        }
        paths = sorted(set(ODS.glob('docker-compose*.yml'))
                       | set(ODS.glob('extensions/services/*/compose*.yaml'))
                       | set(ODS.glob('extensions/library/services/*/compose*.yaml')))
        self.assertGreater(len(paths), 150)
        ports_checked = 0
        for path in paths:
            config = yaml.load(path.read_text(encoding='utf-8'), Loader=ComposeLoader) or {}
            for service, definition in config.get('services', {}).items():
                if (path.relative_to(ODS).as_posix(), service) in entrypoints:
                    continue
                for port in definition.get('ports', []) or []:
                    with self.subTest(path=path.relative_to(ODS), service=service, port=port):
                        if isinstance(port, dict):
                            self.assertEqual(port.get('host_ip'), '127.0.0.1')
                        else:
                            self.assertIsInstance(port, str)
                            self.assertTrue(port.startswith('127.0.0.1:'), port)
                        ports_checked += 1
        self.assertGreater(ports_checked, 150)


if __name__ == '__main__':
    unittest.main()
