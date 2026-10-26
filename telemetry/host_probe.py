"""Read-only Proxmox probe. Collector prepends GUEST_SOURCE and NODE_NAME."""
import concurrent.futures
import datetime
import json
import socket
import os
import shutil
import subprocess
import time
from pathlib import Path


def command(arguments, timeout=10):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=True)
    return result.stdout


def integer(value):
    return None if value in ('-', '', None) else int(value)


HBA_READING = Path('/run/homelab-hba-temp.json')
THERMAL_READING = Path('/run/homelab-thermal.json')


def read_thermal(path=THERMAL_READING):
    try:
        data = json.loads(path.read_text())
        if not isinstance(data.get('temperatures'), list) or not isinstance(data.get('fans'), list):
            return None
        if datetime.datetime.fromisoformat(data['measured_at']).utcoffset() is None:
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def read_hba(path=HBA_READING):
    """Latest HBA chip reading left by hba_temp.py; None when absent or malformed."""
    try:
        data = json.loads(path.read_text())
        return {'roc_temp_c': float(data['roc_temp_c']), 'measured_at': str(data['measured_at'])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def parse_pools(text):
    return [dict(zip(('name', 'total_bytes', 'used_bytes', 'free_bytes', 'health'),
                     (parts[0], int(parts[1]), int(parts[2]), int(parts[3]), parts[4])))
            for line in text.splitlines() if (parts := line.split('\t')) and len(parts) == 5]


def parse_volumes(text):
    return [{'name': p[0], 'type': p[1], 'used_bytes': int(p[2]),
             'available_bytes': integer(p[3]), 'referenced_bytes': int(p[4]),
             'provisioned_bytes': integer(p[5]), 'mountpoint': p[6]}
            for line in text.splitlines() if len(p := line.split('\t')) == 7]


def guest_result(result):
    if not result.get('exited'):
        raise ValueError('Guest resource probe did not finish')
    if result.get('exitcode') != 0:
        raise ValueError('Guest resource probe failed')
    metrics = json.loads(result.get('out-data', ''))
    if not isinstance(metrics, dict) or not {'cpu_percent', 'vcpu_count', 'loadavg', 'memory', 'uptime_seconds', 'disks'} <= metrics.keys():
        raise ValueError('Guest resource probe returned invalid data')
    return metrics


def collect_vm(vm, guest_source):
    record = {'id': int(vm['vmid']), 'name': vm.get('name', 'VM' + str(vm['vmid'])),
              'status': vm['status'], 'vcpu_count': vm.get('cpus'),
              'allocated_memory_bytes': vm.get('maxmem'), 'metrics': None, 'error': None}
    if record['status'] != 'running':
        return record
    if guest_source is None:
        record['error'] = 'Guest metrics are not enabled for this node'
        return record
    try:
        deadline = time.monotonic() + 20
        result = json.loads(command(['qm', 'guest', 'exec', str(record['id']), '--timeout', '10',
                                     '--', 'python3', '-c', guest_source], timeout=14))
        while not result.get('exited') and result.get('pid') is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            pid = result['pid']
            time.sleep(min(0.4, remaining))
            result = json.loads(command(['qm', 'guest', 'exec-status', str(record['id']), str(pid)],
                                        timeout=max(0.1, deadline - time.monotonic())))
            result.setdefault('pid', pid)
        record['metrics'] = guest_result(result)
    except (subprocess.TimeoutExpired, TimeoutError):
        record['error'] = 'Guest resource probe timed out'
    except (subprocess.CalledProcessError, ValueError, KeyError, OSError):
        record['error'] = 'Guest metrics unavailable (guest agent or Python probe unavailable)'
    return record


def collect_host(guest_source, node_name):
    if socket.gethostname() != node_name:
        raise ValueError('Configured Proxmox node does not match host')
    probe = {'__name__': 'guest_probe'}
    exec(compile(guest_source, '<guest_probe>', 'exec'), probe)
    before = probe['cpu_counters']()
    time.sleep(0.3)
    after = probe['cpu_counters']()
    grouped = {}
    for cpu, counters in after.items():
        if cpu == 'cpu' or cpu not in before:
            continue
        topology = Path('/sys/devices/system/cpu') / cpu / 'topology'
        package = int((topology / 'physical_package_id').read_text())
        core = int((topology / 'core_id').read_text())
        grouped.setdefault((package, core), []).append(probe['cpu_percent'](before[cpu], counters))
    cores = [{'id': f'{package}:{core}', 'label': f'Socket {package + 1} / Core {core + 1}',
              'usage_percent': round(sum(values) / len(values), 1)}
             for (package, core), values in sorted(grouped.items())]
    host = {'name': node_name, 'cpu_percent': probe['cpu_percent'](before['cpu'], after['cpu']),
            'physical_cores': len(cores), 'logical_cpus': len(after) - 1, 'cores': cores,
            'memory': probe['memory_info'](), 'loadavg': list(probe['os'].getloadavg()),
            'uptime_seconds': float(Path('/proc/uptime').read_text().split()[0]),
            'pools': [], 'volumes': [], 'sensor_errors': []}
    if shutil.which('zpool') and shutil.which('zfs'):
        try:
            host['pools'] = parse_pools(command(['zpool', 'list', '-Hp', '-o', 'name,size,alloc,free,health']))
            host['volumes'] = parse_volumes(command(['zfs', 'list', '-Hp', '-t', 'filesystem,volume', '-o',
                                                    'name,type,used,available,referenced,volsize,mountpoint']))
        except (OSError, subprocess.SubprocessError, ValueError):
            host['sensor_errors'].append('ZFS measurements unavailable')
    hba = read_hba()
    if hba:
        host['hba'] = hba
    host['thermal'] = read_thermal()
    inventory = (json.loads(command(['pvesh', 'get', f'/nodes/{node_name}/qemu', '--output-format', 'json']))
                 if shutil.which('pvesh') else [])
    if not globals().get('COLLECT_GUESTS', False):
        guest_source = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        vms = list(pool.map(lambda vm: collect_vm(vm, guest_source), sorted(inventory, key=lambda vm: int(vm['vmid']))))
    return {'schema_version': 1, 'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'host': host, 'vms': vms, 'pbs': collect_pbs()}


def collect_pbs():
    """Read local PBS service, filesystem capacity and bounded task history only."""
    if not shutil.which('proxmox-backup-manager'):
        return None
    result = {'service_running': None, 'datastores': [], 'recent_tasks': [], 'error': None}
    try:
        service = subprocess.run(['systemctl', 'is-active', 'proxmox-backup-proxy'],
                                 capture_output=True, text=True, timeout=5)
        result['service_running'] = service.returncode == 0
        stores = json.loads(command(['proxmox-backup-manager', 'datastore', 'list', '--output-format', 'json']))
        for store in stores:
            stat = os.statvfs(store['path'])
            result['datastores'].append({'name': store['name'],
                'total_bytes': stat.f_blocks * stat.f_frsize,
                'used_bytes': (stat.f_blocks - stat.f_bfree) * stat.f_frsize,
                'available_bytes': stat.f_bavail * stat.f_frsize,
                'verify_new': store.get('verify-new'), 'gc_schedule': store.get('gc-schedule')})
        tasks = json.loads(command(['proxmox-backup-manager', 'task', 'list', '--output-format', 'json']))
        # Do not publish users, tokens, task IDs, paths or command output.
        result['recent_tasks'] = [{k: task[k] for k in ('worker_type', 'status', 'starttime', 'endtime')
                                   if k in task} for task in tasks[:20]]
    except (subprocess.SubprocessError, OSError, ValueError, KeyError):
        result['error'] = 'PBS measurements incomplete'
    return result


if __name__ == '__main__':
    print(json.dumps(collect_host(GUEST_SOURCE, NODE_NAME), allow_nan=False))
