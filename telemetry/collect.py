"""Stream read-only probes to the configured host and atomically publish metrics."""
import argparse
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import time

import paramiko
from guest_probe import collect_guest
from host_probe import guest_result

BASE = Path(__file__).resolve().parent


class CollectionDeadline(TimeoutError):
    pass


class GuestIdentityMismatch(ValueError):
    pass


def write_snapshot(path, snapshot):
    data = json.dumps(snapshot, ensure_ascii=True, allow_nan=False, separators=(',', ':')) + '\n'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.metrics-', delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def remote_snapshot(profile):
    ssh = profile['ssh']
    payload = ('GUEST_SOURCE = ' + repr((BASE / 'guest_probe.py').read_text()) + '\n'
               + 'NODE_NAME = ' + repr(profile['node_name']) + '\n'
               + 'COLLECT_GUESTS = ' + repr(profile.get('guest_metrics', False)) + '\n'
               + (BASE / 'host_probe.py').read_text())
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(profile['host'], port=ssh.get('port', 22), username=ssh['user'],
                       key_filename=str(Path(ssh['key_path']).expanduser()), timeout=8, auth_timeout=8, banner_timeout=8,
                       allow_agent=False, look_for_keys=False)
        stdin, stdout, stderr = client.exec_command('snapshot' if profile.get('fixed_probe', True) else 'python3 -', timeout=55)
        if not profile.get('fixed_probe', True):
            stdin.write(payload)
            stdin.flush()
        stdin.channel.shutdown_write()
        output = stdout.read(4 * 1024 * 1024 + 1)
        if len(output) > 4 * 1024 * 1024 or stdout.channel.recv_exit_status() != 0:
            raise RuntimeError('Host resource probe failed')
        snapshot = json.loads(output)
        if snapshot.get('schema_version') != 1 or not isinstance(snapshot.get('host'), dict) or not isinstance(snapshot.get('vms'), list) or not snapshot.get('generated_at'):
            raise ValueError('Invalid host snapshot')
        if snapshot['host'].get('name') != profile['node_name']:
            raise ValueError('Host identity mismatch')
        return snapshot
    finally:
        client.close()


def collect_nodes(profiles, previous=None):
    """Keep healthy nodes current, explicitly marking an unreachable node unknown."""
    previous = previous or {}
    previous_hosts = {h['name']: h for h in previous.get('hosts', [])}
    hosts, vms, pbs, errors = [], [], None, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(profiles)) as pool:
        futures = [pool.submit(remote_snapshot, profile) for profile in profiles]
        for profile, future in zip(profiles, futures):
            name = profile['node_name']
            try:
                result = future.result()
            except Exception:
                errors.append(name)
                host = dict(previous_hosts.get(name, {'name': name}))
                host['error'] = 'Collection unavailable; readings are not current'
                hosts.append(host)
                for vm in previous.get('vms', []):
                    if vm.get('node') == name:
                        vms.append(dict(vm, status='unknown', metrics=None, error=host['error']))
                continue
            hosts.append(dict(result['host'], generated_at=result['generated_at'], error=None))
            vms.extend(dict(vm, node=name) for vm in result['vms'])
            if result.get('pbs') is not None:
                pbs = dict(result['pbs'], node=name, generated_at=result['generated_at'])
    if len(errors) == len(profiles):
        raise RuntimeError('All host probes failed')
    if len({vm['id'] for vm in vms}) != len(vms):
        raise ValueError('Duplicate cluster VM identity')
    return {'schema_version': 2,
            'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'host': hosts[0], 'hosts': hosts, 'vms': sorted(vms, key=lambda vm: vm['id']),
            'pbs': pbs, 'unavailable_nodes': errors}


def add_local_metrics(snapshot, vmid):
    """Explicit operator mapping only; never guess a guest ID from hostname."""
    matches = [vm for vm in snapshot['vms'] if vm['id'] == vmid]
    if len(matches) != 1 or matches[0]['status'] != 'running':
        raise ValueError('Local VM mapping does not match a running VM')
    matches[0]['metrics'] = collect_guest()
    matches[0]['error'] = None


def guest_ssh_payload(expected_mac, guest_source):
    """Check the configured VM's network identity before collecting its metrics."""
    if not isinstance(expected_mac, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', expected_mac):
        raise ValueError('Expected guest network identity is required')
    return ('from pathlib import Path\n'
            + 'import sys\n'
            + 'expected_mac = ' + repr(expected_mac.lower()) + '\n'
            + 'actual_macs = {p.read_text().strip().lower() for p in Path("/sys/class/net").glob("*/address")}\n'
            + 'if expected_mac not in actual_macs:\n    sys.exit(42)\n'
            + guest_source)


def remote_guest_metrics(connection):
    payload = guest_ssh_payload(connection['expected_mac'], (BASE / 'guest_probe.py').read_text())
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(connection['host'], port=connection.get('port', 22), username=connection['user'],
                       key_filename=connection['key_path'], timeout=8, auth_timeout=8, banner_timeout=8,
                       allow_agent=False, look_for_keys=False)
        stdin, stdout, stderr = client.exec_command('snapshot' if connection.get('fixed_probe') else 'python3 -', timeout=10)
        if not connection.get('fixed_probe'):
            stdin.write(payload)
            stdin.flush()
        stdin.channel.shutdown_write()
        output = stdout.read(1024 * 1024 + 1)
        status = stdout.channel.recv_exit_status()
        if status == 42:
            raise GuestIdentityMismatch('Guest network identity did not match')
        if len(output) > 1024 * 1024:
            raise ValueError('Guest output exceeded limit')
        return guest_result({'exited': True, 'exitcode': status, 'out-data': output})
    finally:
        client.close()


def add_guest_ssh_metrics(snapshot, connections):
    for vm in snapshot['vms']:
        if vm['status'] != 'running' or vm.get('metrics') is not None or str(vm['id']) not in connections:
            continue
        try:
            vm['metrics'] = remote_guest_metrics(connections[str(vm['id'])])
            vm['error'] = None
        except CollectionDeadline:
            raise
        except GuestIdentityMismatch:
            vm['error'] = 'Guest metrics unavailable (SSH network identity mismatch)'
        except Exception:
            vm['error'] = 'Guest metrics unavailable (SSH probe failed)'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', help='Single profile; defaults to the connection file default')
    parser.add_argument('--profiles', nargs='+', help='Collect these cluster nodes concurrently')
    parser.add_argument('--profiles-file', type=Path, default=BASE.parent / 'config/hosts.json')
    parser.add_argument('--output', type=Path, default=BASE.parent / 'data/metrics.json')
    parser.add_argument('--local-vmid', type=int, help='Explicit verified VM ID of this collector machine; gather its metrics locally')
    parser.add_argument('--guest-ssh-file', type=Path, help='Optional VM ID to verified SSH connection mapping for guest-agent fallback')
    args = parser.parse_args()
    def expired(_signum, _frame):
        raise CollectionDeadline('Collection deadline reached')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(75)
    started = time.monotonic()
    try:
        config = json.loads(args.profiles_file.read_text())
        names = args.profiles or ([args.profile] if args.profile else list(config['hosts']))
        if len(names) != len(set(names)):
            raise ValueError('Repeated node profile')
        try:
            previous = json.loads(args.output.read_text())
        except (OSError, ValueError):
            previous = None
        snapshot = collect_nodes([config['hosts'][name] for name in names], previous)
        if args.local_vmid is not None:
            add_local_metrics(snapshot, args.local_vmid)
        if args.guest_ssh_file is not None:
            add_guest_ssh_metrics(snapshot, json.loads(args.guest_ssh_file.read_text()))
        write_snapshot(args.output, snapshot)
        print(f"Resource snapshot saved: {len(snapshot['vms'])} VMs in {time.monotonic() - started:.1f}s")
        return 0
    except Exception as error:
        # Remote stderr and exception text can contain credential or command content.
        print(f'Resource collection failed ({type(error).__name__}); previous snapshot retained', file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    sys.exit(main())
