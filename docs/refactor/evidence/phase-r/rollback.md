# v2.6.2 rollback rehearsal (T15f, §10.6)

## Method

The drill runs in a throwaway clone so neither the working tree nor the main repository is touched:

    git clone --local --branch task/t15 /d/project/dsh-blender-plugin D:/DSH/tmp/rollback-drill2

## A. Release rollback (the supported path)

    git checkout refactor-base-v2.6.2      # v2.6.2 release tag, carries lib/ and runtime .mjs

Result (rollback2.log):

- package version 2.6.2, build script is the old 'bash scripts/build.sh' (not needed for a released tag);
- runtime/*.mjs tracked = 17 (v2.6.2 flat tree), runtime/*.ts = 0, lib/ tracked = 7 (release carries lib);
- tsconfig.runtime.json absent, as expected before T15;
- old backend runs without any build: import runtime/engine.mjs -> 87 exports, same fingerprint
  {catalogHash:1d401b938b69, families:34, opsCount:228, toolDetailKeys:7, commandCatalogKeys:39};
- spawn runtime/server.mjs -> /health ok:true, /status plugin.version=2.6.2, runtimeKeys=56;
- ROLLBACK SERVER PASS.

## B. Source rollback of the migration commit

    git checkout -B source-rollback 0a2a395
    git revert --no-edit 0a2a395
    git diff b8778c8 HEAD          # 0 lines

Result: the reverted tree is byte-identical to the pre-migration base (60 runtime .mjs, lib/ tracked again,
tsconfig.runtime.json gone) and the old backend runs from that tree (ROLLBACK SERVER PASS again).

## Limitation recorded honestly

The pre-migration build path (bash scripts/build.sh) requires DSH_CHECKOUT with a DSH packages/ tree.
This host has no usable DSH checkout, so a pre-migration *rebuild* is not executable here. It is not needed
for a release rollback: v2.6.2 ships lib/ in the tag and in the published tarball, so rolling back is a
redeploy of the v2.6.2 tag/tarball, not a source rebuild.
