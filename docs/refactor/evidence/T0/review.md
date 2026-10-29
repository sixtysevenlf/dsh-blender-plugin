# T0 review

- Reviewer: author (self-check). Independent review is not required for the A baseline;
  R1 items referenced by later tasks still need a non-author reviewer at release.
- Risk: the test runner relies on a portable Node 22.17 copied to the user profile
  because of this machine's DSH sandbox restriction. Version is identical to the
  official 22.17.0; the path is environment-specific and documented, not hard-coded
  in the repository.
- Risk: the legacy suite needs a real Blender executable and UTF-8 Python stdout.
  Both are now documented in env.txt; no product code was changed to make it pass.
- surface_fit: catalog already declared fit; runtime/surface.py now registers an
  explicit M3 refusal. This preserves the 228 op count and prevents a fake success.
