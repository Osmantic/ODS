#!/usr/bin/env python3
import os, sys, json, time, signal

STOP = False

def _sig(signum, frame):
    global STOP
    STOP = True

def _read(path, limit=65536):
    try:
        with open(path, "rb") as f:
            return f.read(limit)
    except (OSError, IOError):
        return None

def _read_text(path, limit=65536):
    b = _read(path, limit)
    if b is None:
        return None
    try:
        return b.decode("utf-8", "replace")
    except Exception:
        return None

def _parse_status(text):
    out = {}
    if not text:
        return out
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        if k in ("State", "Threads", "VmRSS", "voluntary_ctxt_switches",
                 "nonvoluntary_ctxt_switches"):
            out[k] = v.strip()
    return out

def _parse_stat(text):
    if not text:
        return None
    # comm may contain spaces/parens; find last ')'
    lp = text.rfind(")")
    if lp < 0:
        return None
    rest = text[lp + 1:].strip()
    fields = rest.split()
    if len(fields) < 20:
        return None
    try:
        ppid = int(fields[1])
        starttime = int(fields[19])
    except (ValueError, IndexError):
        return None
    return {"ppid": ppid, "starttime": starttime}

def _comm(procroot, pid):
    t = _read_text(os.path.join(procroot, str(pid), "comm"))
    if t is None:
        return None
    return t.rstrip("\n")

def _cmdline(procroot, pid):
    b = _read(os.path.join(procroot, str(pid), "cmdline"), 65536)
    if b is None:
        return None
    parts = b.split(b"\x00")
    if parts and parts[-1] == b"":
        parts = parts[:-1]
    return parts

def _match_docker(procroot, pid):
    c = _comm(procroot, pid)
    if c != "docker":
        return False
    parts = _cmdline(procroot, pid)
    if parts is None:
        return False
    try:
        decoded = [p.decode("utf-8", "replace") for p in parts]
    except Exception:
        return False
    if len(decoded) != 4:
        return False
    if os.path.basename(decoded[0]) != "docker":
        return False
    if decoded[1:4] != ["info", "--format", "{{.MemTotal}}"]:
        return False
    return True

def _list_pids(procroot):
    try:
        names = os.listdir(procroot)
    except OSError:
        return []
    out = []
    for n in names:
        if n.isdigit():
            out.append(int(n))
    return out

def _fd_kinds(procroot, pid):
    counts = {"socket": 0, "pipe": 0, "anon": 0, "file": 0}
    fd_dir = os.path.join(procroot, str(pid), "fd")
    try:
        entries = os.listdir(fd_dir)
    except OSError:
        return counts
    for e in entries[:64]:
        try:
            target = os.readlink(os.path.join(fd_dir, e))
        except OSError:
            continue
        if target.startswith("socket:"):
            counts["socket"] += 1
        elif target.startswith("pipe:"):
            counts["pipe"] += 1
        elif target.startswith("anon_inode:"):
            counts["anon"] += 1
        elif target.startswith("/"):
            counts["file"] += 1
    return counts

def _threads(procroot, pid, cap=24):
    out = []
    td = os.path.join(procroot, str(pid), "task")
    try:
        tids = os.listdir(td)
    except OSError:
        return out
    for tid in tids:
        if not tid.isdigit():
            continue
        if len(out) >= cap:
            break
        base = os.path.join(td, tid)
        st = _read_text(os.path.join(base, "stat"))
        state = None
        if st:
            lp = st.rfind(")")
            if lp >= 0:
                rest = st[lp + 1:].strip().split()
                if rest:
                    state = rest[0]
        wchan = _read_text(os.path.join(base, "wchan"))
        if wchan is not None:
            wchan = wchan.strip()
        out.append({"tid": int(tid), "state": state, "wchan": wchan})
    return out

def _sample_pid(procroot, pid):
    base = os.path.join(procroot, str(pid))
    stat_txt = _read_text(os.path.join(base, "stat"))
    if stat_txt is None:
        return None
    parsed = _parse_stat(stat_txt)
    if parsed is None:
        return None
    comm = _comm(procroot, pid)
    if comm is None:
        return None
    status = _parse_status(_read_text(os.path.join(base, "status")))
    wchan = _read_text(os.path.join(base, "wchan"))
    if wchan is not None:
        wchan = wchan.strip()
    rec = {
        "pid": pid,
        "ppid": parsed["ppid"],
        "comm": comm,
        "starttime": parsed["starttime"],
        "wchan": wchan,
        "status": status,
        "fds": _fd_kinds(procroot, pid),
        "threads": _threads(procroot, pid),
    }
    after = _parse_stat(_read_text(os.path.join(base, "stat")))
    return rec if after and after["starttime"] == parsed["starttime"] else None

def scan(procroot="/proc"):
    """Return list of matching docker info PIDs."""
    out = []
    for pid in _list_pids(procroot):
        try:
            if _match_docker(procroot, pid):
                out.append(pid)
        except Exception:
            continue
    return out

def _descendants(procroot, root_pid, max_n=8):
    children = {}
    for pid in _list_pids(procroot):
        st = _read_text(os.path.join(procroot, str(pid), "stat"))
        if not st:
            continue
        p = _parse_stat(st)
        if not p:
            continue
        children.setdefault(p["ppid"], []).append(pid)
    out = []
    stack = list(children.get(root_pid, []))
    while stack and len(out) < max_n:
        p = stack.pop(0)
        out.append(p)
        stack.extend(children.get(p, []))
    return out[:max_n]

def sample(procroot, target_pid):
    """Sample target + up to 8 descendants + one parent."""
    recs = []
    t = _sample_pid(procroot, target_pid)
    if t is None:
        return None
    recs.append(t)
    parent = t["ppid"]
    if parent and parent > 0:
        pr = _sample_pid(procroot, parent)
        if pr is not None:
            recs.append(pr)
    for d in _descendants(procroot, target_pid, 8):
        dr = _sample_pid(procroot, d)
        if dr is not None:
            recs.append(dr)
    return recs

def _open_output(path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    return os.fdopen(fd, "w", encoding="utf-8")

def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: sampler.py OUTPUT.jsonl [--seconds N]\n")
        return 2
    out_path = argv[1]
    seconds = 180
    i = 2
    while i < len(argv):
        if argv[i] == "--seconds" and i + 1 < len(argv):
            try:
                seconds = int(argv[i + 1])
            except ValueError:
                return 2
            i += 2
        else:
            return 2
    if seconds < 1:
        seconds = 1
    if seconds > 300:
        seconds = 300
    if not sys.platform.startswith("linux"):
        sys.stderr.write("linux only\n")
        return 2
    if not os.path.isdir("/proc"):
        sys.stderr.write("no /proc\n")
        return 2

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    try:
        f = _open_output(out_path)
    except OSError as e:
        sys.stderr.write("cannot open output: %s\n" % e)
        return 2

    start = time.monotonic()
    deadline = start + seconds
    records = 0
    max_records = 60
    interval = 0.2
    target_pid = None
    target_start = None
    target_seen_at = None

    try:
        while not STOP and time.monotonic() < deadline and records < max_records:
            now = time.monotonic()
            if target_pid is None:
                pids = scan("/proc")
                if pids:
                    target_pid = pids[0]
                    st = _read_text(os.path.join("/proc", str(target_pid), "stat"))
                    p = _parse_stat(st) if st else None
                    if p:
                        target_start = p["starttime"]
                        target_seen_at = now
            else:
                st = _read_text(os.path.join("/proc", str(target_pid), "stat"))
                p = _parse_stat(st) if st else None
                if p is None or p["starttime"] != target_start or not _match_docker("/proc", target_pid):
                    target_pid = None
                    target_start = None
                    target_seen_at = None
                elif target_seen_at is not None and (now - target_seen_at) >= 1.0:
                    recs = sample("/proc", target_pid)
                    if recs and recs[0]["starttime"] == target_start:
                        for r in recs:
                            r["ts"] = time.time()
                            f.write(json.dumps(r, separators=(",", ":")) + "\n")
                            records += 1
                            if records >= max_records:
                                break
                        f.flush()
            sleep_for = interval - (time.monotonic() - now)
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        try:
            f.flush()
            f.close()
        except Exception:
            pass
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
