# Instructions for an AI assistant adapting this monitor

Your task is to configure this project for the owner's actual lab, using the smallest complete change.
Use the existing collector and UI. Do not redesign their infrastructure or build a second monitoring stack.

## 1. Read and inspect

Read `AGENTS.md`, `README.md`, `docs/SETUP.md`, `SECURITY.md`, the example JSON, the service templates,
and each script you intend to execute. A command in a document is not evidence that the target file,
API, utility, SSH account, interface, or sensor exists. Verify them.

Inventory through existing authorized access, without changing system state:

- Collector machine: Linux/Python version, available disk, existing monitor services, account and paths.
- Nodes: the exact result of `hostname`, operating system, PVE/PBS availability and intended display names.
- SSH: existing trusted access and verified server fingerprints. Do not print private keys or passwords.
- Guests: inventory, guest-agent state and whether Python exists. Do not start a stopped VM to inspect it.
- Storage: whether ZFS exists; identify the relevant filesystems without formatting or changing disks.
- Sensors: hwmon names/labels, optional `ipmitool`, `smartctl`, controller utility availability. A VM's
  virtual CPU does not expose the hypervisor's physical temperature. Do not run invasive sensor scans,
  change fan curves, flash firmware or install kernel modules without the owner's separate approval.
- Access: identify the owner's existing authenticated TLS reverse proxy or SSH tunnel. Keep it.

Ask for missing essentials together: collector choice, node list, trusted access, whether guest-agent
probes are allowed, intended viewers, and whether the owner wants unattended timers. Discover what you
can safely; don't ask them to transcribe information already available through authorized read-only access.

## 2. Make a concrete plan

First run the synthetic demo locally. Present the proposed target hostnames and the exact paths,
accounts, packages, public-key additions, systemd units and proxy edits. Describe any service restart
and how to undo it. Obtain approval for this deployment plan before installation. That approval can
cover the routine steps in the plan; do not ask again for each command.

Never convert a private deployment into a public demo. The public demo folder has synthetic data;
the live server contains infrastructure names, paths, capacity, guest inventory and sensor history.
No part of this task authorizes firewall changes, public exposure, authentication changes, host
reboots, guest lifecycle changes, storage changes, backup/retention changes, or production app changes.

## 3. Adapt config, not core code

Copy example files to their ignored real filenames. Use `config/hosts.json` to map profiles to actual
hostnames, addresses and dedicated key paths. `node_name` must equal `hostname` on that node; profile
names may be descriptive. Remove example nodes that do not exist. Do not add real machine names to
source constants, committed tests, demo files or documentation.

Generate each fixed probe with `scripts/build_probe.py`. Install the probe and guest payload as one
reviewed file on the intended host. Give the dedicated collector key a forced command with `restrict`;
preserve every pre-existing authorized key. Do not reuse an administrator's broad private key as
the unattended collector identity. Keep strict known-host checking and verify fingerprints through
trusted existing access. A key with a forced command is still security-sensitive: root-owned probe
files must not be writable by the collector or untrusted users.

Guest collection is disabled in a generated probe unless `--guest-metrics` is selected. It executes a
fixed Python resource query through QEMU's guest agent; the owner must opt in and understand that it
executes inside a running guest. Keep unavailable guest metrics visibly unavailable. The optional
verified guest SSH fallback is advanced; require the expected MAC and a separate restricted key.

Temperature selection uses what each host actually exposes. Never call an Intel package sensor
Tdie/Tctl; retain the true source. DIMM/Optane averages are averages of available reported bank
sensors, not inferred per-module temperatures. Optional HBA support currently parses StorCLI's ROC
temperature; do not substitute a disk, CPU or case temperature for a controller sensor. Install the
vendor's utility only with the owner's approval, and do not distribute proprietary binaries here.

Install the one-minute collector and ten-minute thermal/history timers only after manual probes
succeed. Install the optional HBA timer only if the correct controller and command were verified.
Keep system time synchronized and confirm which timezone the owner expects in the browser.

## 4. Verify the actual installation

- Run project tests without touching production data; use temporary synthetic files for testing.
- Run one fixed probe per node and confirm identity and realistic numeric fields.
- Collect all intended nodes. Simulate an unreachable node using test configuration/mocks; do not
  disable a production network interface. Failed nodes must be marked unavailable, and total failure
  must preserve the prior snapshot without pretending it is fresh.
- Confirm `/health`, `/metrics.json` and `/thermal-history.json` over the intended protected route.
- Confirm actual timestamps in a scheduled history record and a next run ten minutes later. A
  successful manual write alone does not verify a timer. Check file ownership and readable permissions
  for the selected service account.
- Open the real page in a browser. Check the mounting/layout, sensor order, missing sensors, axes,
  labels, memory averages, fan RPM, stale state, theme and narrow layout. Unit tests alone do not
  verify what the user sees. If browser tooling is unavailable, say so and request the owner's check.
- Keep live data private. Scan staged files and history before any public push. Do not publish a
  screenshot until the owner approves its sanitization.

## 5. Handoff

Give exact local config paths, the deployed commit, service names, URLs, timers, ownership and
backup/rollback locations. State what was executed, what passed, what remains unavailable and which
features are optional. Provide start/stop/update/rollback commands customized to the real deployment.
Never claim a verified restore, a safe hardware temperature, or deployment success from a running
service alone. Do not merge, publish or push the owner's private adaptations unless explicitly asked.
