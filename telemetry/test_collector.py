import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import collect
import guest_probe
import hba_temp
import history
import host_probe


class GuestProbeTests(unittest.TestCase):
    def test_cpu_zero_idle_and_busy(self):
        self.assertEqual(guest_probe.cpu_percent((100, 50), (100, 50)), 0)
        self.assertEqual(guest_probe.cpu_percent((100, 50), (200, 150)), 0)
        self.assertEqual(guest_probe.cpu_percent((100, 50), (200, 50)), 100)

    def test_guest_and_guest_nice_not_double_counted(self):
        self.assertEqual(guest_probe.cpu_counters('cpu 10 0 5 100 2 0 1 0 8 0\n')['cpu'], (118, 102))

    def test_missing_available_memory_is_unknown_not_free(self):
        self.assertEqual(guest_probe.memory_info('MemTotal: 0 kB\nMemFree: 0 kB\n'),
                         {'total_bytes': 0, 'available_bytes': None})

    def test_mounts_keep_real_disks_exclude_runtime(self):
        text = ('/dev/a / ext4 rw 0 0\n/dev/b /srv/restore-staging xfs rw 0 0\n'
                '/dev/c /mnt/my\\040disk ext4 rw 0 0\n/dev/a /var/lib/docker/root ext4 rw 0 0\n'
                'overlay /merged overlay rw 0 0\n/dev/loop0 /snap/foo squashfs rw 0 0\n')
        self.assertEqual(list(guest_probe.persistent_mounts(text)),
                         [('/', 'ext4', '/dev/a'), ('/srv/restore-staging', 'xfs', '/dev/b'), ('/mnt/my disk', 'ext4', '/dev/c')])

    def test_btrfs_subvolumes_count_backing_capacity_once(self):
        mounts = ('/dev/sda2 / btrfs rw 0 0\n/dev/sda1 /boot vfat rw 0 0\n'
                  '/dev/sda2 /home btrfs rw 0 0\n/dev/sda2 /var/log btrfs rw 0 0\n'
                  '/dev/sdb1 /data btrfs rw 0 0\n')
        stats = SimpleNamespace(f_blocks=100, f_bfree=40, f_bavail=35, f_frsize=4096)
        with patch.object(Path, 'read_text', return_value=mounts), \
             patch.object(guest_probe.os.path, 'realpath', side_effect=lambda source: source), \
             patch.object(guest_probe.os, 'stat', return_value=SimpleNamespace(st_dev=10)), \
             patch.object(guest_probe.os, 'statvfs', return_value=stats):
            disks = guest_probe.disks_info()
        self.assertEqual([disk['mountpoint'] for disk in disks], ['/', '/boot', '/data'])


class HostProbeTests(unittest.TestCase):
    def test_pbs_does_not_publish_task_users_paths_or_credentials(self):
        def command(args, **_kwargs):
            if args[1:3] == ['datastore', 'list']:
                return json.dumps([{'name': 'backupstore', 'path': '/private/pbs', 'verify-new': True}])
            if args[1:3] == ['task', 'list']:
                return json.dumps([{'worker_type': 'backup', 'status': 'OK', 'starttime': 1,
                                    'userid': 'private-user', 'upid': 'internal-task-id', 'token': 'secret'}])
            raise AssertionError(args)
        stats = SimpleNamespace(f_blocks=100, f_bfree=40, f_bavail=35, f_frsize=4096)
        with patch.object(host_probe.shutil, 'which', return_value='/usr/sbin/proxmox-backup-manager'), \
             patch.object(host_probe, 'command', side_effect=command), \
             patch.object(host_probe.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
             patch.object(host_probe.os, 'statvfs', return_value=stats):
            result = host_probe.collect_pbs()
        self.assertTrue(result['service_running'])
        self.assertEqual(result['datastores'][0]['available_bytes'], 35 * 4096)
        self.assertEqual(result['recent_tasks'], [{'worker_type': 'backup', 'status': 'OK', 'starttime': 1}])
        for sensitive in ('private', 'secret', 'internal-task-id'):
            self.assertNotIn(sensitive, json.dumps(result))

    def test_failed_pbs_probe_is_incomplete_not_an_empty_healthy_store(self):
        with patch.object(host_probe.shutil, 'which', return_value='/usr/sbin/proxmox-backup-manager'), \
             patch.object(host_probe.subprocess, 'run', side_effect=OSError()):
            result = host_probe.collect_pbs()
        self.assertIsNone(result['service_running'])
        self.assertEqual(result['error'], 'PBS measurements incomplete')

    def test_pool_zero_free_and_missing_volume_size(self):
        self.assertEqual(host_probe.parse_pools('tank\t100\t100\t0\tONLINE\n')[0]['free_bytes'], 0)
        result = host_probe.parse_volumes('tank\tfilesystem\t0\t100\t0\t-\t/tank\n')
        self.assertIsNone(result[0]['provisioned_bytes'])

    def test_stopped_vm_never_invokes_agent(self):
        with patch.object(host_probe, 'command') as command:
            result = host_probe.collect_vm({'vmid': 901, 'status': 'stopped', 'maxmem': 1024}, '')
            command.assert_not_called()
            self.assertIsNone(result['metrics'])
            self.assertIsNone(result['error'])

    def test_running_guest_probe_requires_opt_in(self):
        with patch.object(host_probe, 'command') as command:
            result = host_probe.collect_vm({'vmid': 101, 'status': 'running'}, None)
        command.assert_not_called()
        self.assertIn('not enabled', result['error'])

    def test_missing_agent_is_unavailable_not_fake_metrics(self):
        with patch.object(host_probe, 'command', side_effect=ValueError()):
            result = host_probe.collect_vm({'vmid': 101, 'status': 'running', 'maxmem': 1024}, '')
            self.assertIsNone(result['metrics'])
            self.assertIsNotNone(result['error'])

    def test_guest_must_exit_successfully(self):
        for invalid in ({'exited': 0, 'exitcode': 0}, {'exited': 1, 'exitcode': 1}, {'exited': 1},
                        {'exited': 1, 'exitcode': 0, 'out-data': '{}'}):
            with self.assertRaises(ValueError):
                host_probe.guest_result(invalid)


class HbaTempTests(unittest.TestCase):
    def test_parses_storcli_roc_temperature(self):
        text = 'Status = Success\n\nCtrl_Prop      Value \n-----\nROC temperature(Degree Celsius) 63    \n-----\n'
        self.assertEqual(hba_temp.parse_roc_temp(text), 63)
        self.assertIsNone(hba_temp.parse_roc_temp('Status = Failure\n'))

    def test_probe_reads_reading_and_ignores_missing_or_bad_files(self):
        with tempfile.TemporaryDirectory() as folder:
            good = Path(folder) / 'good.json'
            good.write_text('{"roc_temp_c": 63, "measured_at": "2026-10-25T16:00:00+00:00"}')
            bad = Path(folder) / 'bad.json'
            bad.write_text('{"roc_temp_c": "hot"}')
            self.assertEqual(host_probe.read_hba(good), {'roc_temp_c': 63.0, 'measured_at': '2026-10-25T16:00:00+00:00'})
            self.assertIsNone(host_probe.read_hba(bad))
            self.assertIsNone(host_probe.read_hba(Path(folder) / 'missing.json'))


class HistoryTests(unittest.TestCase):
    def test_appends_snapshot_and_prunes_only_expired_days(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            snapshot = {'schema_version': 2, 'hosts': [{'name': 'compute-01',
                'hba': {'roc_temp_c': 60}, 'thermal': {
                    'temperatures': [{'label': 'Tdie', 'value_c': 42}, {'label': 'Disk', 'value_c': 35}],
                    'fans': [{'label': 'FAN3', 'rpm': 9000}]}}]}
            (data / 'metrics.json').write_text(json.dumps(snapshot))
            (data / 'history').mkdir()
            old, edge, junk = (data / 'history' / n for n in ('2026-07-01.jsonl', '2026-07-27.jsonl', 'notes.jsonl'))
            for path in (old, edge, junk):
                path.write_text('x\n')
            now = datetime.datetime(2026, 10, 25, 16, 0, tzinfo=datetime.timezone.utc)
            history.log_snapshot(data, now)
            history.log_snapshot(data, now + datetime.timedelta(minutes=10))
            lines = (data / 'history' / '2026-10-25.jsonl').read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])['snapshot'], snapshot)
            self.assertFalse(old.exists())
            self.assertTrue(edge.exists() and junk.exists())


class SnapshotTests(unittest.TestCase):
    def node_snapshot(self, name, vmid):
        return {'schema_version': 1, 'generated_at': '2026-10-21T20:00:00+00:00',
                'host': {'name': name}, 'vms': [{'id': vmid, 'status': 'running', 'metrics': {}}]}

    def test_cluster_keeps_node_identity_and_pbs(self):
        primary = self.node_snapshot('compute-01', 101)
        second = self.node_snapshot('backup-01', 102)
        second['pbs'] = {'service_running': True, 'datastores': []}
        with patch.object(collect, 'remote_snapshot', side_effect=lambda p: primary if p['node_name'] == 'compute-01' else second):
            result = collect.collect_nodes([{'node_name': 'compute-01'}, {'node_name': 'backup-01'}])
        self.assertEqual([vm['node'] for vm in result['vms']], ['compute-01', 'backup-01'])
        self.assertEqual(result['pbs']['node'], 'backup-01')
        self.assertEqual(result['unavailable_nodes'], [])

    def test_failed_node_never_gets_current_metrics_or_running_state(self):
        primary = self.node_snapshot('compute-01', 101)
        previous = {'hosts': [{'name': 'backup-01', 'generated_at': 'old', 'cpu_percent': 3}],
                    'vms': [{'id': 102, 'node': 'backup-01', 'status': 'running', 'metrics': {'cpu_percent': 3}}]}
        def probe(profile):
            if profile['node_name'] == 'backup-01':
                raise TimeoutError()
            return primary
        with patch.object(collect, 'remote_snapshot', side_effect=probe):
            result = collect.collect_nodes([{'node_name': 'compute-01'}, {'node_name': 'backup-01'}], previous)
        self.assertEqual(result['unavailable_nodes'], ['backup-01'])
        self.assertEqual(result['hosts'][1]['generated_at'], 'old')
        self.assertTrue(result['hosts'][1]['error'])
        self.assertEqual(result['vms'][1]['status'], 'unknown')
        self.assertIsNone(result['vms'][1]['metrics'])
        self.assertIsNone(result['pbs'])

    def test_complete_outage_does_not_publish_new_timestamp(self):
        with patch.object(collect, 'remote_snapshot', side_effect=TimeoutError()):
            with self.assertRaises(RuntimeError):
                collect.collect_nodes([{'node_name': 'compute-01'}, {'node_name': 'backup-01'}])

    def test_duplicate_vm_identity_is_rejected(self):
        def probe(profile):
            return self.node_snapshot(profile['node_name'], 101)
        with patch.object(collect, 'remote_snapshot', side_effect=probe):
            with self.assertRaises(ValueError):
                collect.collect_nodes([{'node_name': 'compute-01'}, {'node_name': 'backup-01'}])

    def test_guest_ssh_skips_stopped_and_already_collected_vms(self):
        snapshot = {'vms': [{'id': 901, 'status': 'stopped', 'metrics': None},
                            {'id': 100, 'status': 'running', 'metrics': {'cpu_percent': 0}}]}
        with patch.object(collect, 'remote_guest_metrics') as probe:
            collect.add_guest_ssh_metrics(snapshot, {'901': {}, '100': {}})
            probe.assert_not_called()

    def test_guest_mac_mismatch_stops_before_probe_runs(self):
        payload = collect.guest_ssh_payload('02:00:00:00:00:01', "raise AssertionError('Probe must not run')")
        with patch.object(Path, 'glob', return_value=[]):
            with self.assertRaises(SystemExit) as error:
                exec(payload, {'__name__': '__main__'})
        self.assertEqual(error.exception.code, 42)

    def test_guest_ssh_failure_preserves_other_vm_metrics(self):
        snapshot = {'vms': [{'id': 151, 'status': 'running', 'metrics': None},
                            {'id': 100, 'status': 'running', 'metrics': {'cpu_percent': 0}}]}
        with patch.object(collect, 'remote_guest_metrics', side_effect=collect.GuestIdentityMismatch()):
            collect.add_guest_ssh_metrics(snapshot, {'151': {}})
        self.assertIsNone(snapshot['vms'][0]['metrics'])
        self.assertIn('identity mismatch', snapshot['vms'][0]['error'])
        self.assertEqual(snapshot['vms'][1]['metrics'], {'cpu_percent': 0})

    def test_local_metrics_require_explicit_running_vm_match(self):
        snapshot = {'vms': [{'id': 100, 'status': 'stopped', 'metrics': None}]}
        with patch.object(collect, 'collect_guest') as probe:
            for vmid in (100, 101):
                with self.assertRaises(ValueError):
                    collect.add_local_metrics(snapshot, vmid)
            probe.assert_not_called()

    def test_verified_local_vm_gets_real_guest_metrics(self):
        snapshot = {'vms': [{'id': 100, 'status': 'running', 'metrics': None, 'error': 'no agent'}]}
        with patch.object(collect, 'collect_guest', return_value={'cpu_percent': 0}):
            collect.add_local_metrics(snapshot, 100)
        self.assertEqual(snapshot['vms'][0]['metrics'], {'cpu_percent': 0})
        self.assertIsNone(snapshot['vms'][0]['error'])

    def test_invalid_json_preserves_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'metrics.json'
            collect.write_snapshot(path, {'good': True})
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                collect.write_snapshot(path, {'bad': float('nan')})
            self.assertEqual(path.read_bytes(), before)

    def test_atomic_replace_failure_preserves_previous_snapshot_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'metrics.json'
            collect.write_snapshot(path, {'good': True})
            with patch.object(collect.os, 'replace', side_effect=OSError()):
                with self.assertRaises(OSError):
                    collect.write_snapshot(path, {'good': False})
            self.assertEqual(json.loads(path.read_text()), {'good': True})
            self.assertEqual(list(Path(directory).iterdir()), [path])
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)


if __name__ == '__main__':
    unittest.main()
