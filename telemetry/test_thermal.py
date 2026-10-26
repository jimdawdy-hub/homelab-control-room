import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import host_probe
import server
import thermal_probe


class ThermalProbeTests(unittest.TestCase):
    def test_hwmon_keeps_tdie_tctl_zero_rpm_and_distinct_cpu_sockets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for number in (0, 1):
                chip = root / f'hwmon{number}'
                chip.mkdir()
                device = root / f'coretemp.{number}'
                device.mkdir()
                (chip / 'device').symlink_to(device, target_is_directory=True)
                for filename, text in {'name': 'coretemp', 'temp1_label': 'Package id 0',
                                       'temp1_input': '42000', 'temp2_input': '-273150',
                                       'fan1_input': '0'}.items():
                    (chip / filename).write_text(text)
            temps, fans = thermal_probe.hwmon_sensors(root)
            self.assertEqual(len(temps), 2)
            self.assertNotEqual(temps[0]['id'], temps[1]['id'])
            self.assertEqual([sensor['value_c'] for sensor in temps], [42.0, 42.0])
            self.assertEqual(fans[0]['rpm'], 0)
            chip = root / 'hwmon2'
            chip.mkdir()
            for filename, text in {'name': 'k10temp', 'temp1_label': 'Tctl',
                                   'temp1_input': '60500', 'temp2_label': 'Tdie',
                                   'temp2_input': '55500'}.items():
                (chip / filename).write_text(text)
            temps, _ = thermal_probe.hwmon_sensors(root)
            self.assertEqual({s['label']: s['value_c'] for s in temps if s['source'].startswith('k10temp')},
                             {'Tctl': 60.5, 'Tdie': 55.5})

    def test_bmc_keeps_board_cpu_fans_but_ignores_unavailable_and_voltage(self):
        temps, fans = thermal_probe.ipmi_sensors(
            'System Temp | 31.000 | degrees C | ok\nCPU1 Temp | 45 | degrees C | ok\n'
            'FAN3 | 9520 | RPM | ok\nFAN1 | na | | na\n12V | 11.75 | Volts | ok\n')
        self.assertEqual([(s['label'], s['kind']) for s in temps], [('System Temp', 'board'), ('CPU1 Temp', 'cpu')])
        self.assertEqual(fans[0]['rpm'], 9520)
        self.assertEqual(len(fans), 1)

    def test_smart_reading_has_stable_identity_without_publishing_serial(self):
        data = {'serial_number': 'PRIVATE_SERIAL', 'model_name': 'Disk model', 'temperature': {'current': 38}}
        first = thermal_probe.smart_sensor(data, '/dev/sda')
        self.assertEqual(first['id'], thermal_probe.smart_sensor(data, '/dev/sdb')['id'])
        self.assertNotIn('PRIVATE_SERIAL', json.dumps(first))
        self.assertEqual(first['value_c'], 38)
        self.assertIsNone(thermal_probe.smart_sensor({}, '/dev/sda'))

    def test_atomic_cache_and_malformed_or_absent_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'thermal.json'
            self.assertIsNone(host_probe.read_thermal(output))
            data = {'measured_at': '2026-10-26T02:00:00+00:00', 'temperatures': [], 'fans': []}
            thermal_probe.publish(data, output)
            self.assertEqual(host_probe.read_thermal(output), data)
            before = output.read_bytes()
            with self.assertRaises(ValueError):
                thermal_probe.publish({'bad': float('nan')}, output)
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(list(Path(folder).iterdir()), [output])
            output.write_text('{"measured_at":"broken", "temperatures":[], "fans":[]}')
            self.assertIsNone(host_probe.read_thermal(output))

    def test_optional_probe_failures_preserve_hwmon_and_never_publish_raw_errors(self):
        with patch.object(thermal_probe, 'hwmon_sensors', return_value=([{'id': 'cpu'}], [])), \
             patch.object(thermal_probe.shutil, 'which', return_value='/tool'), \
             patch.object(thermal_probe.subprocess, 'run', side_effect=OSError('PRIVATE ERROR')):
            result = thermal_probe.collect_thermal()
        self.assertEqual(result['temperatures'], [{'id': 'cpu'}])
        self.assertEqual(result['errors'], ['BMC sensors unavailable', 'SMART sensor discovery unavailable'])
        self.assertNotIn('PRIVATE ERROR', json.dumps(result))

    def test_smart_uses_standby_checks_and_does_not_query_nvme_twice(self):
        devices = {'devices': [{'name': '/dev/sda', 'type': 'sat'}, {'name': '/dev/nvme0', 'type': 'nvme'}]}
        with patch.object(thermal_probe, 'hwmon_sensors', return_value=([], [])), \
             patch.object(thermal_probe.shutil, 'which', side_effect=lambda name: name if name == 'smartctl' else None), \
             patch.object(thermal_probe, 'run_json', side_effect=[devices, {'temperature': {'current': 35}}]) as query:
            data = thermal_probe.collect_thermal()
        self.assertEqual(query.call_count, 2)
        self.assertEqual(query.call_args.args[0], ['smartctl', '-A', '-n', 'standby', '-j', '-d', 'sat', '/dev/sda'])
        self.assertEqual(data['temperatures'][0]['value_c'], 35)


class ThermalHistoryTests(unittest.TestCase):
    def test_history_projects_deduplicates_and_keeps_actual_measurement_times(self):
        now = datetime.datetime(2026, 10, 26, 2, tzinfo=datetime.timezone.utc)
        measured = (now - datetime.timedelta(minutes=10)).isoformat()
        host = {'name': 'compute-01', 'thermal': {'measured_at': measured,
                'temperatures': [{'id': 'cpu', 'label': 'Tdie', 'source': 'k10temp', 'kind': 'cpu', 'value_c': 42}],
                'fans': [{'label': 'FAN3', 'rpm': 9000}]},
                'hba': {'roc_temp_c': 60, 'measured_at': measured}, 'private': 'DO_NOT_EXPOSE'}
        record = json.dumps({'logged_at': now.isoformat(), 'snapshot': {'hosts': [host]}})
        with tempfile.TemporaryDirectory() as folder:
            history = Path(folder) / 'history'
            history.mkdir()
            (history / '2026-10-26.jsonl').write_text(record + '\nbroken\n' + record + '\n')
            result = server.load_thermal_history(Path(folder), now)
        self.assertEqual(result['interval_seconds'], 600)
        series = result['hosts'][0]['series']
        self.assertEqual([s['id'] for s in series], ['hba:roc', 'cpu'])
        self.assertEqual(series[1]['points'], [(measured, 42)])
        self.assertNotIn('DO_NOT_EXPOSE', json.dumps(result))
        self.assertNotIn('rpm', json.dumps(result))

    def test_history_skips_failed_hosts_old_future_and_nonfinite_samples(self):
        now = datetime.datetime(2026, 10, 26, 2, tzinfo=datetime.timezone.utc)
        hosts = []
        for hours, error, value in ((25, None, 60), (-1, None, 60), (1, 'offline', 60), (1, None, float('nan'))):
            hosts.append({'name': str(hours) + str(value), 'error': error,
                          'hba': {'measured_at': (now - datetime.timedelta(hours=hours)).isoformat(), 'roc_temp_c': value}})
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'history'
            directory.mkdir()
            (directory / '2026-10-26.jsonl').write_text(json.dumps({'snapshot': {'hosts': hosts}}) + '\n')
            self.assertEqual(server.load_thermal_history(Path(folder), now)['hosts'], [])

    def test_empty_history_is_valid_while_new_collector_warms_up(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(server.load_thermal_history(Path(folder))['hosts'], [])

    def test_summary_orders_components_limits_cpu_readings_and_averages_memory(self):
        def sensor(label, value, kind='board', source='BMC'):
            return {'id': label, 'label': label, 'source': source, 'kind': kind, 'value_c': value}
        temperatures = [sensor('Core 0', 40, 'cpu'), sensor('Disk', 35, 'drive'),
                        sensor('CPU2 Temp', 41, 'cpu'), sensor('CPU1 Temp', 42, 'cpu'),
                        sensor('Package id 1', 44, 'cpu'), sensor('Package id 0', 45, 'cpu'),
                        sensor('CPU1_VRMIN Temp', 50), sensor('GPU2 Temp', 48, 'gpu'),
                        sensor('System Temp', 30), sensor('Peripheral Temp', 32), sensor('PCH Temp', 37),
                        sensor('temp1', 36, source='pch_lewisburg · zone0'),
                        sensor('P1_DIMMA~D Temp', 40), sensor('P2_DIMMA~D Temp', 44),
                        sensor('P1_PMEMA~D Temp', 46), sensor('P2_PMEMA~D Temp', 50)]
        host = {'name': 'host', 'hba': {'roc_temp_c': 60, 'measured_at': '2026-10-26T02:00:00+00:00'},
                'thermal': {'measured_at': '2026-10-26T02:00:00+00:00', 'temperatures': temperatures, 'fans': []}}
        result = server.thermal_hosts({'hosts': [host]})[0]['temperatures']
        self.assertEqual([s['label'] for s in result], ['HBA controller', 'CPU #1 · Package', 'CPU #1 · BMC temperature',
                         'CPU #2 · Package', 'CPU #2 · BMC temperature', 'GPU2 Temp', 'PCH', 'System', 'Peripheral',
                         'DIMM average', 'Optane average'])
        self.assertEqual([s['value_c'] for s in result[-2:]], [42, 48])
        self.assertEqual(len(temperatures), 16)  # Summary does not mutate the raw log data.

    def test_tdie_and_tctl_take_priority_over_cpu_fallbacks(self):
        sensors = [{'id': label, 'label': label, 'source': 'k10temp', 'kind': 'cpu', 'value_c': 50}
                   for label in ('Package id 0', 'CPU1 Temp', 'Tctl', 'Tdie')]
        result = server.thermal_readings({'thermal': {'temperatures': sensors, 'measured_at': 'now'}})
        self.assertEqual([s['label'] for s, _ in result], ['CPU #1 · Tdie', 'CPU #1 · Tctl'])

    def test_intel_without_bmc_uses_hottest_core_as_second_socket_reading(self):
        sensors = [{'id': label, 'label': label, 'source': 'coretemp · coretemp.0', 'kind': 'cpu', 'value_c': value}
                   for label, value in (('Package id 0', 40), ('Core 0', 37), ('Core 1', 39))]
        result = server.thermal_readings({'thermal': {'temperatures': sensors, 'measured_at': 'now'}})
        self.assertEqual([s['label'] for s, _ in result], ['CPU #1 · Package', 'CPU #1 · Hottest core'])
        self.assertEqual(result[1][0]['value_c'], 39)

    def test_memory_history_averages_each_measurement_without_inventing_missing_banks(self):
        now = datetime.datetime(2026, 10, 26, 2, tzinfo=datetime.timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'history'
            directory.mkdir()
            lines = []
            for minutes, values in ((20, [40, 44]), (10, [46])):
                sensors = [{'id': str(i), 'label': f'P{i}_DIMM Temp', 'kind': 'board', 'source': 'BMC', 'value_c': value}
                           for i, value in enumerate(values)]
                host = {'name': 'host', 'thermal': {'temperatures': sensors,
                        'measured_at': (now - datetime.timedelta(minutes=minutes)).isoformat()}}
                lines.append(json.dumps({'snapshot': {'hosts': [host]}}))
            (directory / '2026-10-26.jsonl').write_text('\n'.join(lines) + '\n')
            series = server.load_thermal_history(Path(folder), now)['hosts'][0]['series']
            self.assertEqual(len(series), 1)
            self.assertEqual(series[0]['id'], 'memory:dimm:average')
            self.assertEqual([value for _, value in series[0]['points']], [42, 46])


if __name__ == '__main__':
    unittest.main()
