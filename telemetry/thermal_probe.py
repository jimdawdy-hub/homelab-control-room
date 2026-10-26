"""Read host hwmon, BMC and SMART sensors; publish one atomic ten-minute sample."""
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

OUTPUT = Path('/run/homelab-thermal.json')


def reading(value, low=-100, high=200):
    try:
        number = float(value)
        return round(number, 1) if math.isfinite(number) and low <= number <= high else None
    except (TypeError, ValueError):
        return None


def hwmon_sensors(root=Path('/sys/class/hwmon')):
    temperatures, fans = [], []
    for chip in sorted(root.glob('hwmon*')):
        try:
            name = (chip / 'name').read_text().strip()
            device = ((chip / 'device').resolve().name if (chip / 'device').exists()
                      else chip.resolve().parent.parent.name)
        except OSError:
            continue
        kind = ('cpu' if name in ('coretemp', 'k10temp', 'zenpower') else
                'drive' if name in ('nvme', 'drivetemp') else
                'gpu' if name in ('amdgpu', 'nouveau', 'nvidia') else 'board')
        for path in sorted(chip.glob('*_input')):
            match = re.fullmatch(r'(temp|fan)(\d+)_input', path.name)
            if not match:
                continue
            feature = path.name.removesuffix('_input')
            try:
                raw = float(path.read_text())
                label_path = chip / (feature + '_label')
                label = label_path.read_text().strip() if label_path.exists() else feature
            except (OSError, ValueError):
                continue
            sensor = {'id': f'hwmon:{name}:{device}:{feature}', 'label': label,
                      'source': name + ' · ' + device}
            if match[1] == 'temp':
                value = reading(raw / 1000)
                if value is not None:
                    temperatures.append(dict(sensor, kind=kind, value_c=value))
            else:
                value = reading(raw, 0, 100000)
                if value is not None:
                    fans.append(dict(sensor, rpm=value))
    return temperatures, fans


def ipmi_sensors(text):
    temperatures, fans = [], []
    for line in text.splitlines():
        fields = [part.strip() for part in line.split('|')]
        if len(fields) < 4:
            continue
        label, raw, unit, status = fields[:4]
        sensor = {'id': 'bmc:' + label, 'label': label, 'source': 'BMC', 'status': status}
        if unit == 'degrees C' and (value := reading(raw)) is not None:
            kind = ('cpu' if re.match(r'^CPU\d+ Temp$', label) else
                    'gpu' if label.startswith('GPU') else 'board')
            temperatures.append(dict(sensor, kind=kind, value_c=value))
        elif unit == 'RPM' and (value := reading(raw, 0, 100000)) is not None:
            fans.append(dict(sensor, rpm=value))
    return temperatures, fans


def smart_sensor(data, device):
    value = reading(data.get('temperature', {}).get('current'))
    if value is None:
        return None
    # Stable identity without publishing disk serial numbers or WWNs.
    identity = str(data.get('serial_number') or data.get('wwn') or device)
    sensor_id = hashlib.sha256(identity.encode()).hexdigest()[:16]
    model = data.get('model_name') or data.get('model_family') or device
    return {'id': 'smart:' + sensor_id, 'label': model + ' · ' + Path(device).name,
            'source': 'SMART', 'kind': 'drive', 'value_c': value}


def run_json(arguments, timeout=10):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    # smartctl uses nonzero exit bits for disk health warnings even with valid data.
    return json.loads(result.stdout)


def collect_thermal():
    temperatures, fans = hwmon_sensors()
    errors = []
    if shutil.which('ipmitool'):
        try:
            result = subprocess.run(['ipmitool', 'sensor'], capture_output=True, text=True,
                                    timeout=12, check=True)
            temps, speeds = ipmi_sensors(result.stdout)
            temperatures.extend(temps)
            fans.extend(speeds)
        except (OSError, subprocess.SubprocessError):
            errors.append('BMC sensors unavailable')
    if shutil.which('smartctl'):
        try:
            devices = run_json(['smartctl', '--scan', '-j']).get('devices', [])
            for device in devices:
                name = device['name']
                # NVMe temperatures are already exposed through hwmon.
                if device.get('type') == 'nvme':
                    continue
                try:
                    sensor = smart_sensor(run_json(['smartctl', '-A', '-n', 'standby', '-j',
                                                    '-d', device['type'], name], timeout=5), name)
                    if sensor:
                        temperatures.append(sensor)
                except (OSError, subprocess.SubprocessError, ValueError, KeyError):
                    errors.append('SMART temperature unavailable for ' + Path(name).name)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            errors.append('SMART sensor discovery unavailable')
    return {'measured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'temperatures': temperatures, 'fans': fans, 'errors': errors}


def publish(data, output=OUTPUT):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    publish(collect_thermal())
