import pathlib, re, sys
RT = pathlib.Path("/home/sixtyseven67/DSH/dsh-blender-plugin/runtime")
PAT = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*\.dsh_[a-z_]+_api)\s*=\s*\{", re.M)

def uniform(path, dry=False):
    """S1-c：`X.dsh_x_api = {…}` → `X.dsh_x_api = _KIT.Api({…})`（文本级花括号配对，覆盖单行/多行两种形态）。"""
    t = path.read_text(encoding="utf-8")
    if "dsh_kit" not in t:
        return 0
    hits = 0
    while True:
        m = PAT.search(t)
        if not m:
            break
        brace = t.index("{", m.start(), m.end())
        depth, i = 0, brace
        while i < len(t):
            c = t[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if i >= len(t):
            return hits  # 括号不配对，放弃（保持原样）
        t = t[:brace] + "_KIT.Api(" + t[brace:i + 1] + ")" + t[i + 1:]
        hits += 1
        if hits > 50:
            break
    if hits and not dry:
        path.write_text(t, encoding="utf-8")
    return hits

args = sys.argv[1:]
dry = "--dry" in args
names = [a for a in args if not a.startswith("--")]
tot = 0
for nm in (names or sorted(p.stem for p in RT.glob("*.py"))):
    p = RT / (nm if nm.endswith(".py") else nm + ".py")
    if not p.exists():
        continue
    h = uniform(p, dry=dry)
    if h:
        print("  + %-18s 统一 %d 处" % (p.name, h))
        tot += h
print("%s：%d 处 API 统一为 _KIT.Api" % ("dry-run" if dry else "已统一", tot))