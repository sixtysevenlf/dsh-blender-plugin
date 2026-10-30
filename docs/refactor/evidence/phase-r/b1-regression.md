# B1 3-question small regression — NOT EXECUTED (handed to T16/D4)

Plan §7.2 T15f and §8.1 D4: 3 questions x P0/P1 with the fixed v2.6.1 plugin, archived separately and
not mixed with 3.0.0 results.

## What was checked on this host

- D:/DSH/benchmark exists with b1_runner.py, the B1 focus lists, and the WSL harness dependencies.
- WSL Ubuntu has /opt/bench/venv (Python 3.12.3), /opt/blender, /opt/bench/BlenderWorld, and dsh 0.2.1-rc.3.
- D:/secrets/Deepseek/1.txt..3.txt exist and are readable from WSL.
- But ~/.dsh does not exist for the WSL user (HOME=/home/villager_uncle): the profiles
  bench-m / bench-p0 / bench-p1 are absent, so b1_runner.py cannot launch any arm, and the fixed
  v2.6.1 plugin is not installed in WSL.
- The Windows environment has no DEEPSEEK_API_KEY / ANTHROPIC_API_KEY, and no GUI Blender session.

## Verdict

NOT EXECUTED. No numbers were produced or claimed. Handed to T16/D4: provisioning the B1 profiles and
installing the fixed v2.6.1 plugin are release-environment work, not T15 code work.
