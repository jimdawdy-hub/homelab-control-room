# Contributing

Use synthetic fixtures and documentation-only `.example.invalid` hosts. Keep local deployments out of
the source tree's tracked files. Before a PR, run the Python and Node tests in README, the public audit,
and `git diff --cached --check`. Check the actual UI in a browser when changing layout or behavior;
state verification limits. Do not expose somebody else's lab to reproduce a bug.

Prefer small fixes and configuration-driven behavior. Do not add hardcoded node names, network
addresses, automatic host-key acceptance, public binding defaults or unnecessary dependencies.
