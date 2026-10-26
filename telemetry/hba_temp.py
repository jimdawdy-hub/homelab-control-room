"""Writes the SAS HBA chip temperature to /run for host_probe.py. Run as root by a timer."""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

STORCLI = '/usr/local/sbin/storcli64'
OUTPUT = '/run/homelab-hba-temp.json'


def parse_roc_temp(text):
    match = re.search(r'ROC temperature\(Degree Celsius\)\s+(\d+)', text)
    return int(match.group(1)) if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storcli', default=STORCLI)
    parser.add_argument('--controller', default='/c0')
    args = parser.parse_args()
    if not re.fullmatch(r'/c\d+', args.controller):
        parser.error('Controller must be /c followed by its numeric index')
    result = subprocess.run([args.storcli, args.controller, 'show', 'temperature'], capture_output=True, text=True, timeout=60)
    temperature = parse_roc_temp(result.stdout)
    if temperature is None:
        sys.exit('no ROC temperature in storcli output')
    reading = {'roc_temp_c': temperature,
               'measured_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    temporary = OUTPUT + '.tmp'
    with open(temporary, 'w') as handle:
        json.dump(reading, handle)
    os.chmod(temporary, 0o644)
    os.replace(temporary, OUTPUT)


if __name__ == '__main__':
    main()
