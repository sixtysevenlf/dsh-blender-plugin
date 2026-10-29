# T3 review

- Reviewer: author (self-check).
- The fake addon is test-only and listens on an ephemeral port; it never binds 9876/9877.
- Tests use the real AddonClient, so framing/queue/close behavior is exercised, but no
  real Blender process or backend server is started.
- Timeout test uses a 200 ms deadline and asserts a lower bound (180 ms), not an exact
  duration, to avoid flakiness on loaded machines.
