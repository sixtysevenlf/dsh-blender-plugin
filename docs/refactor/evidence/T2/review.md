# T2 review

- Reviewer: author (self-check). A6 is R1 in §8; independent review deferred to the
  release gate.
- Tests assert real registries (catalogPayload, apply() via a fake host context, the
  built client bundle) rather than only snapshots.
- The tool-schema test uses the built lib/index.js deliberately: the package consumer
  sees that artifact. T15e will switch unit/integration imports to TS after Phase R.
- The lossless test uses the host's real snapshotJsonValue, not a local copy.
- route-contract is a static safety net; full per-route HTTP behavior belongs to T7/T14.
