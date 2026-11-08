# -*- coding: utf-8 -*-
"""Split from qc_render.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403

def _lock_holder_default():
    """holder 缺省：`DSH_SESSION#<pid>` → `DSH_RUN_ID#<pid>` → `pid-<pid>`。

    为什么带 pid（v0.9.1 定稿）：同一 DSH 会话里**并发起多个无头进程**是常见用法
    （batch / 多成员），只用会话名会让它们同名 → 互相误放锁。带上 pid 后同会话内也彼此唯一。
    """
    h = os.environ.get("DSH_SESSION") or os.environ.get("DSH_RUN_ID")
    return ("%s#%d" % (h, os.getpid())) if h else ("pid-%d" % os.getpid())


def _lock_base(override=None):
    return str(override) if override else _work_dir()


def _lock_path(base=None):
    """锁文件路径：<base>/locks/render.lock（base 默认 _work_dir()；WSL 路径自动转 Windows 侧）。"""
    return _win(os.path.join(_lock_base(base), "locks", "render.lock"))


def _lock_read(path):
    """读锁文件 → (info|None, err)。

    err == "missing" 表示锁不存在（调用方按"无人持有"处理，不是错误）；
    解析不了时返回 {"_unparsable": True, "_mtime": ..., "_raw": ...} —— 不猜持有者，只按 mtime 判年龄。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return None, "missing"
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:120])
    try:
        info = json.loads(raw)
        if not isinstance(info, dict):
            raise ValueError("不是 JSON 对象")
        return info, None
    except Exception as e:
        mt = None
        try:
            mt = float(os.path.getmtime(path))
        except Exception:
            pass
        return {"_unparsable": True, "_mtime": mt, "_raw": str(raw)[:200]}, "解析失败：%s" % str(e)[:120]


def _lock_age(info, path):
    """锁的年龄（秒）+ 判据来源（"acquired_at" | "mtime" | None）。**只认时间戳**，不看进程存活。"""
    if isinstance(info, dict) and info.get("acquired_at") not in (None, ""):
        try:
            return max(0.0, time.time() - float(info["acquired_at"])), "acquired_at"
        except Exception:
            pass
    try:
        return max(0.0, time.time() - float(os.path.getmtime(path))), "mtime"
    except Exception:
        return None, None


def _lock_holder_of(info):
    if not isinstance(info, dict) or info.get("_unparsable"):
        return None
    h = info.get("holder")
    return str(h) if h not in (None, "") else None


def _lock_line(res):
    """锁结果 → 返回体/jsonl 里的统一 lock 行（计时口径可审计：等了多久、谁持有、有没有打破过期锁）。"""
    if not res:
        return {"enabled": False, "holder": None, "waited_ms": 0, "acquired": False,
                "stale_broken": False, "path": None, "reason": "lock=false"}
    return {"enabled": True, "holder": res.get("holder"), "waited_ms": int(res.get("waited_ms") or 0),
            "acquired": bool(res.get("acquired")), "stale_broken": bool(res.get("stale_broken")),
            "path": res.get("path")}


def _lock_acquire(path, holder, ttl_s, wait_s=0.0, note=""):
    """原子取锁（O_CREAT|O_EXCL）；被占则**按时间戳判 TTL**：过期就打破（记 stale_broken），否则轮询 wait_s。"""
    t0 = time.perf_counter()
    ttl = float(ttl_s) if ttl_s else LOCK_DEFAULT_TTL_S
    wait = max(0.0, float(wait_s or 0.0))
    deadline = t0 + wait
    stale_broken, stale_note = False, None
    parse_err = None
    while True:
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            fd = None
        except Exception as e:
            return {"ok": False, "action": "acquire", "acquired": False, "holder": holder, "path": path,
                    "waited_ms": int(round((time.perf_counter() - t0) * 1000.0)),
                    "error": "锁文件建不出来：%s: %s" % (type(e).__name__, str(e)[:160])}
        if fd is not None:
            payload = {"holder": str(holder), "pid": os.getpid(), "acquired_at": time.time(),
                       "ttl_s": ttl, "note": str(note or "")}
            try:
                os.write(fd, _j(payload).encode("utf-8"))
                try:
                    os.fsync(fd)          # 落盘：别的进程读到的必须是完整 JSON（不是写了一半的）
                except Exception:
                    pass
            finally:
                try:
                    os.close(fd)
                except Exception:
                    pass
            return {"ok": True, "action": "acquire", "acquired": True, "holder": str(holder),
                    "pid": os.getpid(), "acquired_at": payload["acquired_at"], "ttl_s": ttl,
                    "path": path, "waited_ms": int(round((time.perf_counter() - t0) * 1000.0)),
                    "stale_broken": stale_broken, "stale_note": stale_note, "parse_error": parse_err,
                    "note": str(note or ""),
                    "hint": "渲染完务必 release：render_lock(action=\"release\", holder=%r%s)"
                            % (str(holder), (", dir=%r" % os.path.dirname(os.path.dirname(path))) if path else "")}
        # ---- 已被占：读它、按**时间戳**判过期
        info, err = _lock_read(path)
        age, age_src = _lock_age(info, path)
        if err is not None and err != "missing":
            parse_err = err
        cur_holder = _lock_holder_of(info)
        stale = (err == "missing") or (age is not None and age > ttl)
        if stale:
            try:
                os.remove(path)
                stale_broken = True
                stale_note = ("过期锁被打破：holder=%s age=%.1fs(%s) > ttl=%.1fs%s"
                              % (cur_holder or "?", (age if age is not None else -1.0), age_src or "?",
                                 ttl, ("；锁文件解析失败：%s" % parse_err) if parse_err else ""))
            except FileNotFoundError:
                stale_broken = True          # 别的进程先打破了：下一轮 O_EXCL 自己建
                stale_note = "锁在读取后被别的进程打破/释放（race；本轮直接重试创建）"
            except Exception as e:
                return {"ok": False, "action": "acquire", "acquired": False, "holder": holder, "path": path,
                        "waited_ms": int(round((time.perf_counter() - t0) * 1000.0)),
                        "stale_broken": stale_broken,
                        "error": "锁过期了但删不掉：%s: %s（权限/占用？）" % (type(e).__name__, str(e)[:160])}
            continue                         # 立刻重试 O_EXCL（不 sleep）
        if time.perf_counter() >= deadline:
            return {"ok": False, "action": "acquire", "acquired": False, "holder": holder,
                    "current_holder": cur_holder, "path": path, "ttl_s": ttl,
                    "holder_age_s": (round(age, 1) if age is not None else None),
                    "holder_age_source": age_src, "parse_error": parse_err,
                    # 还要等多久对方才过期（拿不到年龄时 null，不猜）：wait_s 小于它就别死等
                    "holder_ttl_remaining_s": (round(max(0.0, ttl - age), 1) if age is not None else None),
                    "waited_ms": int(round((time.perf_counter() - t0) * 1000.0)),
                    "stale_broken": stale_broken, "stale_note": stale_note,
                    "error": "渲染锁被占用：等满 wait_s=%.1fs 仍未取得（当前 holder=%s%s）"
                             % (wait, cur_holder or "?",
                                ("，已持有 %.1fs" % age) if age is not None else "，持有时间读不到"),
                    "hint": "调大 wait_s（或 qc_render_views 的 lock_wait_s）；查持有者 render_lock(action=\"status\")；"
                            "确认对方进程已死可 render_lock(action=\"release\", holder=<holder>) 或等 TTL 过期"
                            "（holder_ttl_remaining_s 是还要等多久）"}
        time.sleep(min(LOCK_POLL_S, max(0.0, deadline - time.perf_counter())))


def _lock_release(path, holder):
    """释放锁：**只有 holder 匹配才删**（不匹配 → ok:false 并报当前持有者，绝不误删别人的锁）。"""
    info, err = _lock_read(path)
    if err == "missing":
        return {"ok": True, "action": "release", "released": False, "holder": str(holder), "path": path,
                "reason": "锁文件不存在（从未取得 / 已被释放 / 已过期被打破）"}
    if err is not None or info.get("_unparsable"):
        return {"ok": False, "action": "release", "released": False, "holder": str(holder), "path": path,
                "current_holder": None, "parse_error": err,
                "error": "锁文件解析不了，无法确认持有者 → 拒绝删除（防误删别人的锁）",
                "hint": "等 TTL 过期后会被下一个 acquire 打破；或人工确认后删 %s" % path}
    cur = _lock_holder_of(info)
    if cur != str(holder):
        return {"ok": False, "action": "release", "released": False, "holder": str(holder), "path": path,
                "current_holder": cur,
                "error": "holder 不匹配，拒绝释放（当前持有者=%s）" % (cur or "?")}
    try:
        os.remove(path)
    except FileNotFoundError:
        return {"ok": True, "action": "release", "released": False, "holder": str(holder), "path": path,
                "reason": "锁在释放前已被别的进程打破/释放（幂等，不算失败）"}
    except Exception as e:
        return {"ok": False, "action": "release", "released": False, "holder": str(holder), "path": path,
                "error": "删锁失败：%s: %s" % (type(e).__name__, str(e)[:160])}
    return {"ok": True, "action": "release", "released": True, "holder": str(holder), "path": path,
            "held_ms": (int(round((time.time() - float(info.get("acquired_at"))) * 1000.0))
                        if info.get("acquired_at") else None)}


def _lock_status(path, ttl_s=None):
    """查锁：当前持有者 / 已持有多久 / 剩余 TTL / 是否已过期（只读，不改任何东西）。"""
    info, err = _lock_read(path)
    if err == "missing":
        return {"ok": True, "action": "status", "path": path, "held": False, "holder": None,
                "hint": "当前没有锁（可以 acquire）"}
    age, age_src = _lock_age(info, path)
    if info.get("_unparsable"):
        ttl = float(ttl_s or LOCK_DEFAULT_TTL_S)
        return {"ok": True, "action": "status", "path": path, "held": True, "holder": None,
                "parse_error": err, "raw": info.get("_raw"), "ttl_s": ttl,
                "age_s": (round(age, 1) if age is not None else None), "age_source": age_src,
                "remaining_s": (max(0.0, ttl - age) if age is not None else None),
                "stale": (age is not None and age > ttl),
                "note": "锁文件解析不了：持有者未知（不猜），过期判定退到文件 mtime"}
    ttl = float(ttl_s or info.get("ttl_s") or LOCK_DEFAULT_TTL_S)
    return {"ok": True, "action": "status", "path": path, "held": True,
            "holder": _lock_holder_of(info), "pid": info.get("pid"), "ttl_s": ttl,
            "acquired_at": info.get("acquired_at"), "age_s": (round(age, 1) if age is not None else None),
            "age_source": age_src, "remaining_s": (max(0.0, ttl - age) if age is not None else None),
            "stale": (age is not None and age > ttl), "note": info.get("note") or ""}


def render_lock(args=None):
    """跨进程渲染锁原语（v0.9.1 E1）：action="acquire" | "release" | "status"。

    blender_op(op="qc_render_lock", args={"action": "acquire", "holder": "me"})   # 或 plan(op="render_lock")
    K.dsh_qc_render_api("render_lock", {"action": "status"})

    参数：
      action  acquire（默认 status）| release | status
      holder  持有者名（缺省 = DSH_SESSION → DSH_RUN_ID → pid-<pid>）
      ttl_s   租约秒数（默认 600）：**过期判定只认锁文件里的 acquired_at 时间戳**
      wait_s  取不到时的等待上限秒数（默认 0 = 立即返回 ok:false；轮询间隔 0.25s）
      note    备注（写进锁文件，便于别人看出你在跑什么）
      dir     锁基址目录（默认 _work_dir()）→ 锁文件 <dir>/locks/render.lock
    """
    a = _unpack_args(args)
    action = str(a.get("action") or a.get("op") or "status").strip().lower()
    holder = str(a.get("holder") or _lock_holder_default())
    note = str(a.get("note") or "")
    base = a.get("dir") or a.get("lock_dir") or a.get("lock_base")
    path = _lock_path(base)
    try:
        ttl_s = float(a.get("ttl_s") if a.get("ttl_s") is not None else LOCK_DEFAULT_TTL_S)
        wait_s = float(a.get("wait_s") or 0.0)
    except Exception as e:
        return _j({"ok": False, "action": action, "error": "ttl_s/wait_s 不是数字：%s" % str(e)[:120],
                   "path": path})
    warnings = []
    if ttl_s <= 0:
        warnings.append("ttl_s=%r ≤ 0：已按默认 %.0fs 处理（TTL≤0 会让锁一写下就被判过期，等于没有锁）"
                        % (a.get("ttl_s"), LOCK_DEFAULT_TTL_S))
        ttl_s = LOCK_DEFAULT_TTL_S
    if wait_s < 0:
        warnings.append("wait_s=%r < 0：已按 0 处理" % (a.get("wait_s"),))
        wait_s = 0.0
    if action in ("acquire", "acquire_lock", "get", "lock"):
        res = _lock_acquire(path, holder, ttl_s, wait_s, note)
    elif action in ("release", "unlock", "free"):
        res = _lock_release(path, holder)
    elif action in ("status", "state", "check"):
        res = _lock_status(path, ttl_s)
    else:
        return _j({"ok": False, "action": action, "path": path,
                   "error": "action 不认（只认 acquire/release/status）",
                   "actions": ["acquire", "release", "status"]})
    out = dict(res)
    out["version"] = QC_RENDER_VERSION
    out["lock_dir"] = os.path.dirname(os.path.dirname(path))
    out["holder_default"] = (str(holder) == _lock_holder_default())
    if warnings:
        out["warnings"] = warnings
    if out.get("stale_broken"):
        out.setdefault("warnings", []).append(out.get("stale_note") or "打破了过期锁")
    return _j(out)
