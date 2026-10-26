"""Read-only, standard-library guest resource probe; streamed through QEMU agent."""
import json
import os
import re
import time
from pathlib import Path


def cpu_counters(text=None):
    text = Path('/proc/stat').read_text() if text is None else text
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if fields and re.fullmatch(r'cpu\d*', fields[0]):
            values = [int(value) for value in fields[1:9]]
            result[fields[0]] = (sum(values), values[3] + values[4])
    return result


def cpu_percent(before, after):
    total = after[0] - before[0]
    idle = after[1] - before[1]
    return round(max(0.0, min(100.0, 100 * (total - idle) / total)), 1) if total > 0 else 0.0


def memory_info(text=None):
    text = Path('/proc/meminfo').read_text() if text is None else text
    values = {line.split(':', 1)[0]: int(line.split()[1]) * 1024
              for line in text.splitlines() if ':' in line}
    return {'total_bytes': values['MemTotal'], 'available_bytes': values.get('MemAvailable')}


def persistent_mounts(text):
    filesystems = {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'vfat', 'exfat', 'ntfs', 'ntfs3', 'fuseblk', 'zfs'}
    excluded = ('/proc', '/sys', '/dev', '/run', '/snap', '/var/lib/docker', '/var/lib/containerd')
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[2] not in filesystems:
            continue
        mount = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[1])
        if any(mount == prefix or mount.startswith(prefix + '/') for prefix in excluded):
            continue
        source = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[0])
        yield mount, fields[2], source


def disks_info():
    disks, seen = [], set()
    for mount, filesystem, source in sorted(persistent_mounts(Path('/proc/mounts').read_text())):
        try:
            # Btrfs gives subvolumes distinct st_dev values despite sharing one
            # capacity pool. Keep its first mount (root first) per backing device.
            device = ('btrfs', os.path.realpath(source)) if filesystem == 'btrfs' else os.stat(mount).st_dev
            if device in seen:
                continue
            stats = os.statvfs(mount)
            total, free = stats.f_blocks * stats.f_frsize, stats.f_bavail * stats.f_frsize
            disks.append({'mountpoint': mount, 'filesystem': filesystem,
                          'total_bytes': total, 'free_bytes': free,
                          'used_bytes': (stats.f_blocks - stats.f_bfree) * stats.f_frsize})
            seen.add(device)
        except OSError:
            continue
    return disks


def collect_guest():
    before = cpu_counters()['cpu']
    time.sleep(0.3)
    after = cpu_counters()['cpu']
    return {'cpu_percent': cpu_percent(before, after), 'vcpu_count': os.cpu_count(),
            'loadavg': list(os.getloadavg()), 'memory': memory_info(),
            'uptime_seconds': float(Path('/proc/uptime').read_text().split()[0]),
            'disks': disks_info()}


if __name__ == '__main__':
    print(json.dumps(collect_guest(), allow_nan=False))
