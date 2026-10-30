# 0003. Ship the addon as a package and a zip, released with 3.0.0

- **Status**: Accepted
- **Date**: 2026-10-30
- **Deciders**: plugin maintainers
- **Target**: 3.0.0, tasks T9 and T16

## Context

The Blender addon is one file: `addon/dsh_blender_bridge.py`, 7,515 lines and about 323 KB. Users
install it through **Edit > Preferences > Add-ons > Install**, then enable it and click **Connect**. It
registers 39 commands and speaks protocol 11 to the backend; the launcher checks for addon v1.10 or newer.

A single file this size has three costs. Review is effectively impossible: a one-line change is buried in
a file nobody reads end to end. Import order is fragile: everything is one module namespace, so a mistake
in one section can break registration. And release coupling is invisible: the plugin and the addon version
must move together, but nothing in the artifact says so.

Blender 4.2 LTS and 5.x can install an addon directory as a zip archive. Blender's extension platform is a
separate distribution channel with its own signing and metadata requirements; it is not a substitute for
the offline zip path this project supports.

## Decision

The addon SHALL ship as a package directory `addon/dsh_blender_bridge/` containing `__init__.py`
(with `bl_info`) and modules for protocol, commands, selection, and UI, and as a release zip built from
that directory.

- At 3.0.0 the addon version is **1.11**, protocol stays **11**, and `bl_info` declares the package
  name, version, and minimum Blender version.
- All 39 commands remain registered and behave identically; a register/unregister round trip is part of
  the addon tests.
- The zip is attached to the GitHub release; the npm package contains the addon directory, not the legacy
  single file.
- The launcher and the plugin both refuse a protocol other than 11 and name the expected addon version in
  the error.

**Migration for existing users** (documented in `MIGRATIONS.md` and the READMEs):

1. In Blender, disable and remove the old single-file addon.
2. Install the `dsh_blender_bridge-1.11.zip` release asset as an addon.
3. Enable **DSH Blender Bridge**, click **Connect**, then restart DSH if the plugin was upgraded too.
4. Verify `doctor` returns `kind=ok` (GUI route) or the worker plus `see` returns a frame (headless).

**Rollback** is symmetric: remove the zip addon, reinstall the v2.6.2 single file from that tag, enable it
and Connect; the backend and protocol are unchanged, so no data migration is involved. The DSH plugin can
stay at 3.0.0 while the addon is rolled back only if the launcher still accepts addon 1.10; otherwise
reinstall the plugin as well.

## Consequences

- The addon becomes reviewable and testable in units; the register/unregister path becomes a real test.
- Packaging has two artifacts (zip and npm package) that must be built and verified together; the release
  checklist covers both.
- Users must remove the old addon explicitly. Silent coexistence is impossible because both register the
  same operator names; the migration step is required, not optional.
- Blender extension-platform publication stays out of scope; it can be added later without changing the
  package layout.

## Alternatives considered

- **Keep the single file.** Rejected: it is the main maintainability problem this phase exists to fix.
- **Publish only via the Blender extension platform.** Rejected for 3.0.0: offline and older-4.x installs
  still need the zip, and the platform adds signing and review latency.
- **Publish a pip package.** Blender does not install addons from pip; users would still need a zip.
- **Split into several addons.** Registration conflicts and a harder install story; one package with
  modules is enough.

## Links

- Packaging and tarball checks: [ADR 0004](0004-lib-build-release.md)
- Migration notes: [MIGRATIONS.md](../../MIGRATIONS.md)
