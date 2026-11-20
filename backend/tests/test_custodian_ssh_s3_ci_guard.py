"""Read-only catalogue/workflow contracts; no runtime imports or target work."""
import ast
import hashlib
import json
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parents[2]
RUNTIME = REPO / 'ops/backup/custodian_v2/synthetic_ssh_s3'
PROOFS = REPO / 'ops/backup/tests/custodian_v2/synthetic_ssh_s3'
WORKFLOW = REPO / '.github/workflows/custodian_synthetic_ssh_s3.yml'
RUNTIME_NAMES = set('action_child action_host action_profile action_protocol action_root bounded_ssh controller_entry live_durable_protocol live_event_store ssh_helper ssh_ingress'.split())
PROOF_NAMES = set('native_actor_adapter native_controller_adapter native_ssh_admission native_storage_chain native_storage_gate run_local_gate run_native_ssh_gate test_action_lifecycle test_controller_entry test_live_event_store test_ssh_helper test_ssh_ingress test_support fetch_openssh_supply test_fetch_supply'.split())

def text(name):
    return (RUNTIME / name).read_text(encoding='utf-8')

class SyntheticSshS3Guard(unittest.TestCase):
    def test_exact_source_catalogue_without_authority(self):
        catalogue = json.loads((RUNTIME / 'source-profile.json').read_bytes())
        self.assertEqual(set(catalogue), {'format', 'status', 'admitted_operations', 'working_credentials_included', 'sources'})
        self.assertEqual(catalogue['format'], 'pult-synthetic-ssh-s3-ci-catalogue-v1')
        self.assertEqual(catalogue['status'], 'SOURCE_CI_ONLY_NO_PRODUCTION_PERMIT')
        self.assertEqual(catalogue['admitted_operations'], [])
        self.assertIs(catalogue['working_credentials_included'], False)
        self.assertEqual({p.stem for p in RUNTIME.glob('*.py')}, RUNTIME_NAMES)
        self.assertEqual({p.stem for p in PROOFS.glob('*.py')}, PROOF_NAMES)
        inputs = [*RUNTIME.glob('*.py'), *PROOFS.glob('*.py'), PROOFS/'native-source-pins.json',
            PROOFS/'openssh-supply/SUPPLY.json', PROOFS/'.gitignore', WORKFLOW, Path(__file__),
            REPO/'docs/custodian-synthetic-ssh-s3.md', REPO/'.gitattributes']
        for p in inputs:
            relative = p.relative_to(REPO).as_posix()
            self.assertIn(relative, catalogue['sources'])
        for relative, expected in catalogue['sources'].items():
            self.assertFalse(Path(relative).is_absolute())
            self.assertNotIn('..', Path(relative).parts)
            self.assertEqual(hashlib.sha256((REPO/relative).read_bytes()).hexdigest(), expected, relative)

    def test_frozen_predecessors_are_exact(self):
        self.assertEqual(hashlib.sha256((REPO/'ops/backup/custodian_v2/source-profile.json').read_bytes()).hexdigest(),
            '8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3')
        pins = json.loads((PROOFS/'native-source-pins.json').read_bytes())
        for relative, expected in pins.items():
            self.assertEqual(hashlib.sha256((REPO/relative).read_bytes()).hexdigest(), expected, relative)

    def test_compile_without_importing_or_creating_bytecode(self):
        for p in [*RUNTIME.glob('*.py'), *PROOFS.glob('*.py')]:
            compile(p.read_bytes(), str(p), 'exec', dont_inherit=True, optimize=0)

    def test_live_transport_cannot_inject_clients_or_skip_tls(self):
        source = text('live_event_store.py')
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == '_PinnedLiveStore')
        constructor = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
        self.assertEqual([a.arg for a in constructor.args.kwonlyargs], ['endpoint','region','bucket','binding','credential','peer_ipv4'])
        self.assertIsNone(constructor.args.kwarg)
        for item in ('ssl.create_default_context()', 'ssl.CERT_REQUIRED', 'context.check_hostname',
            'trust_env=False', 'retries=0', 'follow_redirects=False', "extensions={'sni_hostname':self._host}",
            "'if-none-match':'*'", "region!='ru-3'", 'peer.is_global', 'peer.is_multicast'):
            self.assertIn(item, source)
        for item in ('verify=False', 'MockTransport', 'http://', 'os.environ', 'DeleteObject', 'ListObjects'):
            self.assertNotIn(item, source)

    def test_permanent_fence_claims_slots_before_any_launch(self):
        source = text('action_root.py')
        self.assertIn('self.claim_role_slots(); s = self.profile.spec', source)
        self.assertIn('os.O_EXCL', source)
        self.assertIn('self._role_slots_claimed', source)
        self.assertIn('permanent_fence_retained=True', source)
        tree = ast.parse(source)
        removers = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == 'unlink']
        self.assertEqual(len(removers), 1)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Action')
        close = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'close_admission')
        self.assertIn(removers[0], list(ast.walk(close)))
        self.assertIn("directory = path(s['admission']['root'])", ast.get_source_segment(source, close))
        self.assertNotIn("s['control']", ast.get_source_segment(source, close))
        self.assertIn('durable DISPATCHED before PUT', text('live_durable_protocol.py'))

    def test_native_proofs_keep_distinct_boundaries_and_real_faults(self):
        ssh = (PROOFS/'run_native_ssh_gate.py').read_text()
        for item in ("len(report['cases'])==9", "report['storage_action_not_executed']", "report['provider_proven'] is False", "report['full_chain_proven'] is False"):
            self.assertIn(item, ssh)
        storage = (PROOFS/'native_storage_gate.py').read_text()
        for item in ("'write_unknown_after_put'", "'read_fail_get_error'", "r['methods'],r['fault_activation']", 'explicit_mock_s3=True', 'real_openssh=False', 'provider_tls_iam=False', 'actor_private_netns_verified'):
            self.assertIn(item, storage)
        for p in RUNTIME.glob('*.py'):
            self.assertNotIn('native_actor_adapter', p.read_text())
            self.assertNotIn('MockTransport', p.read_text())

    def test_ci_has_no_secrets_mutation_or_unpinned_actions(self):
        source = WORKFLOW.read_text()
        for item in ('contents: read', 'persist-credentials: false', 'timeout-minutes: 12',
            'windows-2022', 'ubuntu-24.04', '--require-hashes', '--only-binary=:all:', '--no-deps',
            'test_custodian_ssh_s3_ci_guard.py', 'run_local_gate.py', 'test_fetch_supply.py',
            'fetch_openssh_supply.py', 'run_native_ssh_gate.py', 'native_storage_gate.py --reviewed-by-main',
            'Reject optimized proof entry points'):
            self.assertIn(item, source)
        for item in ('secrets.', 'id-token:', 'write-all', 'pull_request_target', '--admin', '--auto', 'pip install -U', 'apt-get install', 'curl ', 'deploy'):
            self.assertNotIn(item, source)
        uses = [line.strip().split('uses: ', 1)[1] for line in source.splitlines() if 'uses: ' in line]
        self.assertEqual(len(uses), 4)
        for action in uses:
            self.assertRegex(action, r'^actions/(checkout|setup-python)@[0-9a-f]{40}$')

    def test_hashed_external_text_is_lf_on_windows_too(self):
        source = (REPO/'.gitattributes').read_text()
        for relative in ('.github/workflows/custodian_synthetic_ssh_s3.yml',
            'backend/tests/test_custodian_ssh_s3_ci_guard.py', 'docs/custodian-synthetic-ssh-s3.md'):
            self.assertIn(relative+' text eol=lf', source)

    def test_ci_colon_commands_are_not_ambiguous_plain_scalars(self):
        for row in WORKFLOW.read_text().splitlines():
            if row.lstrip().startswith('run: '):
                command = row.lstrip().removeprefix('run: ')
                self.assertNotIn(': ', command, 'colon command requires a YAML block scalar')

if __name__ == '__main__':
    unittest.main()
