import importlib.util
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, BASE / 'scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class SetupTests(unittest.TestCase):
    def test_readme_documentation_links_exist_in_the_distributed_checkout(self):
        for target in re.findall(r'\]\(([^)]+)\)', (BASE / 'README.md').read_text()):
            if not target.startswith(('http:', 'https:', '#')):
                self.assertTrue((BASE / target.split('#')[0]).is_file(), target)

    def test_generated_probe_binds_node_and_disables_guest_execution_by_default(self):
        build = module('build_probe').build
        source = build('example-node')
        compile(source, '<generated>', 'exec')
        self.assertIn("NODE_NAME = 'example-node'", source)
        self.assertIn('COLLECT_GUESTS = False', source)
        self.assertIn('COLLECT_GUESTS = True', build('example-node', True))
        with self.assertRaises(ValueError):
            build('node; command')

    def test_audit_finds_secrets_and_addresses_without_printing_them(self):
        audit = module('audit_public')
        token = 'gh' + 'p_' + 'A' * 36
        address = '.'.join(['10', '20', '30', '40'])
        findings = audit.audit_text(token + '\n' + address, 'fixture')
        self.assertEqual(len(findings), 2)
        self.assertNotIn(token, str(findings))
        self.assertNotIn(address, str(findings))
        self.assertEqual(audit.audit_text('127.0.0.1 compute.example.invalid 192.0.2.10', 'docs'), [])
        assignment = '{"token":"' + 'B' * 24 + '"}'
        self.assertTrue(any('credential assignment' in item for item in audit.audit_text(assignment, 'fixture')))

    def test_audit_checks_deleted_historical_files(self):
        audit = module('audit_public')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args):
                subprocess.run(['git', '-C', folder, *args], check=True, capture_output=True)
            git('init', '-q')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (root / 'note.txt').write_text('.'.join(['10', '20', '30', '40']))
            git('add', 'note.txt'); git('commit', '-qm', 'fixture')
            git('rm', 'note.txt'); git('commit', '-qm', 'remove fixture')
            findings, _, objects = audit.audit_repository(root, history=True)
            self.assertTrue(objects)
            self.assertTrue(any('private IPv4' in finding for finding in findings))
