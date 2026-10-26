# Manual setup

This guide targets a Linux collector and Linux/Proxmox nodes. Paths below are a proposed installation
layout, not claims about your machines. Review commands before running them. Start with one node.

## 1. Prepare the collector

Clone the repository, make a Python 3.11+ venv, and install `requirements.txt`. Create an untracked `data/`
directory and copy `config/hosts.example.json` to `config/hosts.json`. Optionally copy the dashboard
example to `config/dashboard.json` and replace its title/bookmarks. Never commit either real JSON file.

Use your actual node's `hostname` as `node_name`, your real trusted SSH target as `host`, and the absolute
path of a dedicated collector key as `ssh.key_path`. Remove unused example profiles. The default collector
collects all profiles; use `--profiles profile-a profile-b` to select a subset. Names and VM IDs are not hardcoded.

The `*.example.invalid` addresses are intentionally non-routable placeholders. Do not replace the
example files in Git with your actual settings.

## 2. Install a fixed probe on each monitored node

On your workstation/collector, generate a node-specific file without contacting the node:

```sh
mkdir -p .generated
python3 scripts/build_probe.py --node YOUR_EXACT_HOSTNAME --output .generated/host_probe.py
```

Add `--guest-metrics` only if you want fixed Linux resource probes inside running guests through QEMU
guest agent. A guest needs an already-enabled/working agent and Python 3. No VM is started by the probe.
If disabled or unavailable, the UI still shows configured guest inventory without invented utilization.

Review the generated file. Through your existing administrative access, install it on the node as
`/usr/local/lib/homelab-monitor/host_probe.py`, root-owned and mode `0644`, in a root-owned `0755` directory.
Install `telemetry/thermal_probe.py` in the same directory. The generated file embeds the guest source;
the collector does not upload arbitrary commands during normal collection.

Create a dedicated Ed25519 key on the collector, owned by its service account and mode `0600`. Verify
the node's SSH host-key fingerprint through a trusted console/administrative connection, then add it
to that account's `~/.ssh/known_hosts`. Do not disable host-key checking or blindly trust a network scan.

Append this **public-key** entry to the intended node's root `authorized_keys`, preserving other entries:

```text
restrict,command="/usr/bin/python3 -B /usr/local/lib/homelab-monitor/host_probe.py" ssh-ed25519 YOUR_COLLECTOR_PUBLIC_KEY
```

Root access is used for PVE inventory, guest-agent queries, PBS local status and hardware tools.
The forced command confines this key to the fixed probe. Do not give it unrestricted shell access.
Use a dedicated account with carefully reviewed sudo permissions instead only if you understand and
adapt every required command; that alternative is not configured automatically by this project.

From the collector, test the restricted key with your own trusted SSH target:

```sh
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -i /PATH/TO/COLLECTOR_KEY YOUR_SSH_TARGET snapshot
```

It should return one JSON snapshot identifying the expected node. Do not paste its raw output publicly.

## 3. Verify optional sensors

Run `thermal_probe.py` on the physical node that owns the hardware. hwmon requires no Python package.
Local IPMI needs a working `ipmitool`/BMC interface; SMART needs `smartctl`. Install these separately
only if appropriate for your host. Missing optional tools do not stop CPU/memory collection.

```sh
sudo python3 -B /usr/local/lib/homelab-monitor/thermal_probe.py
```

It writes `/run/homelab-thermal.json` atomically. SMART queries use standby checks so sleeping disks
are not deliberately woken. The fixed host probe reads this cache; it does not scan hardware every minute.
Fan RPM requires a driver/BMC that exposes it. Some systems expose no board/fan/memory sensors.

Optional HBA: install `hba_temp.py` beside the other probe scripts only after verifying a compatible
StorCLI controller sensor. Obtain StorCLI from its vendor; no vendor binary is included here.

```sh
sudo python3 /usr/local/lib/homelab-monitor/hba_temp.py --storcli /YOUR/VERIFIED/STORCLI_PATH --controller /c0
```

Replace `/c0` with the verified controller index. The script reads temperature only and writes
`/run/homelab-hba-temp.json`. A missing/stale controller reading must not be treated as healthy.

## 4. Collect and start the real dashboard

From the repository on the collector:

```sh
mkdir -p data
.venv/bin/python telemetry/collect.py --profiles-file config/hosts.json --output data/metrics.json
.venv/bin/python telemetry/history.py --data-dir data
.venv/bin/python telemetry/server.py --data-dir data --port 8080
```

Open **http://127.0.0.1:8080**. The server binds loopback by default and serves only allowlisted assets
and read-only endpoints. It has no built-in authentication. Use your own authenticated TLS reverse
proxy or an authenticated SSH tunnel for remote viewing. Never expose this live dashboard directly
to the Internet. The public showcase is separate because even a read-only live dashboard contains
private infrastructure information.

`/health` reports `503` for data older than three minutes. `/metrics.json` still returns a stale snapshot
for diagnosis, clearly marked stale. `/thermal-history.json` projects the last 24 hours of summary
temperatures, not full archived logs. Service links reject credentials and query strings in URLs.

## 5. Make it unattended

Review every template in `systemd/` before installation. Collector/web/history units expect this repo
and venv at `/opt/homelab-control-room`, and a dedicated `homelab-monitor` service account with home
`/var/lib/homelab-control-room`. Adapt the paths/account to your installation consistently; do not
recursively chown an existing shared directory. Keep source read-only to that account; give it write
access only to the new `data/` directory, and read access to its local config and key.

For a new production installation, prepare the expected account and directories explicitly. Check
`getent passwd homelab-monitor` first; create the account only if it is absent. Do not overwrite an
existing `/opt/homelab-control-room` checkout or service account without inspecting it.

```sh
# Create the account only if missing:
sudo useradd --system --create-home --home-dir /var/lib/homelab-control-room --shell /usr/sbin/nologin homelab-monitor
# Clone only into a new, absent directory:
sudo git clone https://github.com/jimdawdy-hub/homelab-control-room.git /opt/homelab-control-room
sudo python3 -m venv /opt/homelab-control-room/.venv
sudo /opt/homelab-control-room/.venv/bin/pip install -r /opt/homelab-control-room/requirements.txt
sudo install -d -o homelab-monitor -g homelab-monitor -m 0700 /opt/homelab-control-room/data
sudo install -d -o homelab-monitor -g homelab-monitor -m 0700 /var/lib/homelab-control-room/.ssh
sudo -u homelab-monitor ssh-keygen -t ed25519 -N '' -f /var/lib/homelab-control-room/.ssh/monitor_ed25519
```

Create the real JSON configuration separately and install it root-owned, group `homelab-monitor`,
mode `0640`. Add the previously verified node host keys to that account's `known_hosts`, with mode
`0600`. Confirm the manual collection under `sudo -u homelab-monitor` before enabling timers. Copy only
the three collector service files and two collector timer files into `/etc/systemd/system/`; install
the thermal pair separately on the physical hosts. Inspect each destination before replacing it and
retain any earlier version as a private rollback copy.

On each physical node, install and enable `homelab-thermal.service` and `homelab-thermal.timer` after
the manual sensor command succeeds. HBA units are optional and require the correct vendor path first.
On the collector, install the collect, history and web units after the manual collection succeeds.
Do not enable all templates blindly on every node.

```sh
sudo systemctl daemon-reload
# On a monitored host:
sudo systemctl enable --now homelab-thermal.timer
# On the collector, after its account/config/venv are prepared:
sudo systemctl enable --now homelab-collect.timer homelab-history.timer homelab-web.service
```

Collection runs every minute. Thermals run at minutes 00/10/20/etc.; history at 02/12/22/etc. after a
resource refresh, to allow caches to settle. History retains daily JSONL files for 90 days. Existing
records are not rewritten or fabricated. Verify a scheduled write and both next-run schedules:

```sh
systemctl list-timers 'homelab-*'
systemctl status homelab-collect.service homelab-history.service
journalctl -u homelab-collect.service -u homelab-history.service -n 30
curl --fail http://127.0.0.1:8080/health
```

Time labels use the viewer's timezone. Records use UTC timestamps. Keep hosts' clocks synchronized.

## Update and rollback

Before updating, record the current Git SHA and copy the ignored local config, installed host probes,
unit files and runtime history to a private backup location. Review the incoming diff. Updating the
collector code alone does not update fixed probes: regenerate/review/install them on their matching
hosts, preserving the same restricted public-key entries. Restart `homelab-web.service` after UI/server
changes; timers load updated unit files after `daemon-reload` and a timer restart.

Rollback means returning the source to the recorded SHA, restoring the matching installed probes and
units/config from those backups, reloading systemd, and restarting this monitor's services. Do not
delete history or change unrelated keys, firewall rules, VMs, pools or backups to roll this tool back.
