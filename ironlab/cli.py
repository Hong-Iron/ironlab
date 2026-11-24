"""ironlab CLI — 서버의 REST API를 쓰는 얇은 클라이언트.

설정은 ~/.config/ironlab/config.json 에 저장 (IRONLAB_URL, IRONLAB_TOKEN 환경변수가 우선).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

CFG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ironlab" / "config.json"


# ───────────────────────── 기반 ─────────────────────────
def load_cfg() -> dict:
    cfg = json.loads(CFG_PATH.read_text()) if CFG_PATH.exists() else {}
    cfg["url"] = os.environ.get("IRONLAB_URL", cfg.get("url", "http://127.0.0.1:8700"))
    cfg["token"] = os.environ.get("IRONLAB_TOKEN", cfg.get("token", ""))
    return cfg


class Client:
    def __init__(self, cfg: dict):
        headers = {"Authorization": f"Bearer {cfg['token']}"} if cfg.get("token") else {}
        self.http = httpx.Client(base_url=cfg["url"].rstrip("/"), headers=headers, timeout=60)

    def req(self, method: str, path: str, **kw):
        try:
            r = self.http.request(method, path, **kw)
        except httpx.HTTPError as e:
            die(f"서버에 연결할 수 없습니다: {e}")
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            die(f"{r.status_code}: {detail}")
        ct = r.headers.get("content-type", "")
        return r.json() if "application/json" in ct else r.text

    def get(self, path, **kw): return self.req("GET", path, **kw)
    def post(self, path, **kw): return self.req("POST", path, **kw)


def die(msg: str) -> None:
    print(f"ironlab: {msg}", file=sys.stderr)
    sys.exit(1)


def gb(b: float) -> str:
    return f"{b / 2**30:.1f}G"


def human(b: float) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if b < 1024:
            return f"{b:.0f}{unit}" if unit == "B" else f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}P"


def table(rows: list[list], headers: list[str]) -> str:
    rows = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    line = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()
    return "\n".join([line(headers), line(["─" * w for w in widths]), *map(line, rows)])


def ago(ts) -> str:
    if not ts:
        return "-"
    s = time.time() - ts
    return "방금" if s < 60 else f"{int(s // 60)}분 전" if s < 3600 else f"{int(s // 3600)}시간 전" if s < 86400 else f"{int(s // 86400)}일 전"


def dur(s) -> str:
    if s is None:
        return "-"
    s = int(max(0, s))
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


# ───────────────────────── 명령 ─────────────────────────
def cmd_login(a, c):
    CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg = {"url": a.url, "token": a.token}
    me = Client(cfg).get("/api/me")
    CFG_PATH.write_text(json.dumps(cfg))
    os.chmod(CFG_PATH, 0o600)
    print(f"{me['name']} ({me['role']}) 로 로그인했습니다 → {CFG_PATH}")


def render_status(o: dict) -> str:
    g, h, q = o["gpu"], o["host"], o["queue"]
    out = []
    if g.get("available"):
        out.append(f"GPU  {g['name']}  {g['util']}%  VRAM {g['used_mb'] / 1024:.1f}/{g['total_mb'] / 1024:.0f}G  "
                   f"{g['temp']:.0f}°C  {g.get('power_w') or '-'}W")
    else:
        out.append(f"GPU  사용 불가: {g.get('error')}")
    out.append(f"HOST CPU {h['cpu_percent']:.0f}%  RAM {gb(h['mem_used'])}/{gb(h['mem_total'])}  "
               f"DISK {gb(h['disk_used'])}/{gb(h['disk_total'])}  load {h['load'][0]}")
    out.append(f"QUEUE 실행 {q['running']}  대기 {q['queued']}  예약 가능 {q['budget_gb']}G")
    for a in o.get("alerts", []):
        out.append(f"⚠  {a}")
    rows = []
    for u in o["users"]:
        ws = u["workspace"]
        rows.append([("● " if u["active"] else "  ") + u["name"],
                     f"on {dur(time.time() - ws['started_at'])}" if ws and ws["state"] == "running" else "off",
                     u["ssh_sessions"] or "-", f"{u['cpu_cores']:.1f}/{u['cpu_limit']:g}",
                     human(u["mem_bytes"]), f"{u['vram_mb'] / 1024:.1f}G" if u["vram_mb"] else "-",
                     u["jobs_running"] or "-", f"{human(u['disk_used'])}/{human(u['disk_quota'])}",
                     ago(u["last_seen"])])
    out.append("")
    out.append(table(rows, ["사용자", "워크스페이스", "SSH", "CPU", "RAM", "VRAM", "작업", "디스크", "최근"]))
    procs = [p for p in g.get("processes", [])]
    if procs:
        out.append("")
        out.append(table([[p["pid"], p.get("user") or "(호스트)",
                           f"job #{p['job_id']}" if p.get("kind") == "job" else p.get("kind"),
                           f"{p['used_mb'] / 1024:.1f}G"] for p in sorted(procs, key=lambda p: -p["used_mb"])],
                         ["PID", "사용자", "출처", "VRAM"]))
    return "\n".join(out)


def cmd_status(a, c):
    print(render_status(c.get("/api/overview")))


def cmd_top(a, c):
    try:
        while True:
            text = render_status(c.get("/api/overview"))
            sys.stdout.write("\033[H\033[2J" + time.strftime("%H:%M:%S") + "  (Ctrl+C로 종료)\n\n" + text + "\n")
            sys.stdout.flush()
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print()


def cmd_ws(a, c):
    if a.action == "start":
        w = c.post("/api/workspace/start")
    elif a.action == "stop":
        w = c.post("/api/workspace/stop")
    else:
        w = c.get("/api/workspace")
    print(f"상태: {w['state']}")
    print(f"할당: CPU {w['limits']['cpus']}코어, RAM {w['limits']['mem_gb']}GB, GPU 공유")
    print(f"접속: {w['ssh_command']}")
    if not w["has_ssh_key"]:
        print("※ SSH 키가 없습니다: ironlab key set ~/.ssh/id_ed25519.pub")


def cmd_key(a, c):
    keys = Path(a.file).expanduser().read_text()
    r = c.req("PUT", "/api/me/ssh-keys", json={"keys": keys})
    print(f"SSH 키 {r['count']}개를 저장했습니다. 워크스페이스를 다시 시작하면 적용됩니다.")


def cmd_job_submit(a, c):
    command = " ".join(a.command) if a.command else sys.stdin.read()
    body = {"command": command, "vram_gb": a.vram or 1, "name": a.name or "",
            "exclusive": a.exclusive, "workdir": a.workdir or ""}
    if a.cpus:
        body["cpus"] = a.cpus
    if a.mem:
        body["mem_gb"] = a.mem
    j = c.post("/api/jobs", json=body)
    print(f"작업 #{j['id']} 제출 ({'GPU 독점' if j['exclusive'] else str(j['vram_gb']) + 'GB'})")
    if a.follow:
        follow_logs(c, j["id"])


def cmd_job_ls(a, c):
    jobs = c.get("/api/jobs", params={"scope": "all" if a.all else "mine", "limit": a.limit})
    rows = []
    for j in jobs:
        el = (j["finished_at"] or time.time()) - j["started_at"] if j["started_at"] else None
        status = j["status"] + (f"({j['exit_code']})" if j["exit_code"] not in (None, 0) else "")
        rows.append([j["id"], j["user"], status, "독점" if j["exclusive"] else f"{j['vram_gb']:g}G",
                     dur(el) if el is not None else f"대기 {dur(time.time() - j['created_at'])}",
                     (j["wait_reason"] or j["name"])[:60]])
    print(table(rows, ["ID", "사용자", "상태", "VRAM", "시간", "이름/대기 사유"]) if rows else "작업이 없습니다")


def follow_logs(c, job_id: int):
    seen = ""
    last_reason = None
    try:
        while True:
            j = c.get(f"/api/jobs/{job_id}")
            if j["status"] == "queued":
                reason = j.get("wait_reason") or "스케줄러 대기"
                if reason != last_reason:
                    print(f"… 대기 중: {reason}", file=sys.stderr)
                    last_reason = reason
                time.sleep(2)
                continue
            text = c.get(f"/api/jobs/{job_id}/logs", params={"tail": 0})
            if text.startswith(seen):
                sys.stdout.write(text[len(seen):])
            else:
                sys.stdout.write(text)
            sys.stdout.flush()
            seen = text
            j = c.get(f"/api/jobs/{job_id}")
            if j["status"] not in ("queued", "running"):
                print(f"\n── 작업 #{job_id} {j['status']} (exit {j['exit_code']})")
                return
            time.sleep(2)
    except KeyboardInterrupt:
        print()


def cmd_job_logs(a, c):
    if a.follow:
        follow_logs(c, a.id)
    else:
        sys.stdout.write(c.get(f"/api/jobs/{a.id}/logs", params={"tail": a.tail}))


def cmd_job_cancel(a, c):
    j = c.post(f"/api/jobs/{a.id}/cancel")
    print(f"작업 #{j['id']}: {j['status']}")


def split_remote(spec: str) -> tuple[str, str]:
    """'scratch:data/x' → ('scratch', 'data/x'), 'x' → ('home', 'x')."""
    if ":" in spec and spec.split(":", 1)[0] in ("home", "scratch", "shared"):
        area, path = spec.split(":", 1)
        return area, path
    return "home", spec


def cmd_files_ls(a, c):
    area, path = split_remote(a.path or "")
    d = c.get("/api/files", params={"area": area, "path": path})
    rows = [[("d " if e["is_dir"] else "  ") + e["name"] + ("/" if e["is_dir"] else ""),
             "" if e["is_dir"] else human(e["size"]), ago(e["mtime"])] for e in d["entries"]]
    print(f"{area}:/{d['path']}" + ("" if d["writable"] else "  (읽기 전용)"))
    print(table(rows, ["이름", "크기", "수정"]) if rows else "(비어 있음)")


def cmd_files_put(a, c):
    area, rdir = split_remote(a.dest or "")
    for local in a.local:
        p = Path(local)
        if not p.is_file():
            die(f"파일이 아닙니다: {local} (폴더는 rsync/scp를 쓰세요)")
        with p.open("rb") as f:
            r = c.post("/api/files/upload", data={"area": area, "path": rdir},
                       files={"file": (p.name, f)})
        print(f"↑ {p.name} ({human(r['size'])}) → {area}:/{rdir}")


def cmd_files_get(a, c):
    area, path = split_remote(a.remote)
    dest = Path(a.local or Path(path).name)
    with c.http.stream("GET", "/api/files/download", params={"area": area, "path": path}) as r:
        if r.status_code >= 400:
            r.read()
            die(f"{r.status_code}: {r.text}")
        with dest.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    print(f"↓ {area}:/{path} → {dest}")


def cmd_files_rm(a, c):
    area, path = split_remote(a.path)
    c.req("DELETE", "/api/files", params={"area": area, "path": path, "recursive": a.recursive})
    print(f"삭제: {area}:/{path}")


def cmd_files_mkdir(a, c):
    area, path = split_remote(a.path)
    parent, _, name = path.rstrip("/").rpartition("/")
    c.post("/api/files/mkdir", json={"area": area, "path": parent, "name": name})
    print(f"생성: {area}:/{path}")


def cmd_files_usage(a, c):
    q = c.get("/api/files/usage")
    print(f"홈 {human(q['home_used'])} / {human(q['quota'])} ({q['home_used'] / max(q['quota'], 1) * 100:.0f}%)  "
          f"스크래치 {human(q['scratch_used'])}")


def cmd_user_ls(a, c):
    users = c.get("/api/admin/users")
    print(table([[u["name"], u["role"] + (" (비활성)" if u["disabled"] else ""), u["tailscale_login"] or "-",
                  f"{u['cpus']:g}c/{u['mem_gb']}G", f"{u['disk_gb']}G",
                  f"{u['max_vram_gb']:g}G" if u["max_vram_gb"] else "-", u["ssh_port"]] for u in users],
                ["이름", "권한", "Tailscale", "CPU/RAM", "디스크", "VRAM한도", "SSH포트"]))


def _user_body(a) -> dict:
    return {k: v for k, v in {"role": "admin" if getattr(a, "admin", False) else getattr(a, "role", None),
                              "tailscale_login": a.tailscale, "cpus": a.cpus, "mem_gb": a.mem,
                              "disk_gb": a.disk, "max_vram_gb": a.max_vram}.items() if v is not None}


def cmd_user_add(a, c):
    r = c.post("/api/admin/users", json={"name": a.name, **_user_body(a)})
    print(f"사용자 {r['name']} 생성 (SSH 포트 {r['ssh_port']})")
    print(f"토큰 (한 번만 표시): {r['token']}")


def cmd_user_set(a, c):
    body = _user_body(a)
    if a.disable:
        body["disabled"] = True
    if a.enable:
        body["disabled"] = False
    c.req("PATCH", f"/api/admin/users/{a.name}", json=body)
    print(f"{a.name} 수정 완료 (워크스페이스를 다시 시작하면 CPU/RAM 변경이 적용됩니다)")


def cmd_user_token(a, c):
    r = c.post(f"/api/admin/users/{a.name}/token")
    print(f"{r['name']} 새 토큰: {r['token']}")


def cmd_events(a, c):
    ev = c.get("/api/admin/events", params={"limit": a.limit})
    for e in reversed(ev):
        print(f"{time.strftime('%m-%d %H:%M:%S', time.localtime(e['ts']))}  {e['user'] or '-':10s}  "
              f"{e['kind']:22s} {e['detail']}")


def cmd_llm_status(a, c):
    d = c.get("/api/llm")
    m = d.get("model")
    state = {"ready": "사용 가능", "loading": "올리는 중", "off": "내려가 있음", "error": "오류"}.get(d["state"], d["state"])
    print(f"상태: {state}" + (f"  ({d['error']})" if d.get("error") else ""))
    if m:
        used = d.get("vram_used_gb")
        print(f"모델: {m['name']}  VRAM {m['vram_gb']:g}GB 예약" + (f" (지금 실제 {used:.1f}GB)" if used else "")
              + f"  컨텍스트 {m['ctx']} / 동시 {m['parallel']} (요청당 {m['ctx'] // max(1, m['parallel'])})")
        print(f"올린 사람: {d.get('loaded_by') or '-'}  마지막 사용: {ago(d.get('last_used'))}")
    print(f"등록된 모델: {', '.join(x['name'] for x in d['models']) or '없음'}")
    print(f"API: {d['endpoints']['external']}  (워크스페이스 안: {d['endpoints']['internal']})")
    rows = [[u["user"], u["requests"], u["prompt_tokens"] or 0, u["completion_tokens"] or 0] for u in d["usage_today"]]
    if rows:
        print("\n오늘 사용량")
        print(table(rows, ["사용자", "요청", "입력 토큰", "출력 토큰"]))


def cmd_llm_load(a, c):
    d = c.post("/api/llm/load", json={"name": a.name})
    print(f"{a.name}: {d['state']} — 올라가는 데 30초~1분쯤 걸립니다 (ironlab llm status 로 확인)")


def cmd_llm_unload(a, c):
    c.post("/api/llm/unload")
    print("모델을 내렸습니다")


def cmd_llm_chat(a, c):
    prompt = " ".join(a.prompt) if a.prompt else sys.stdin.read()
    body = {"messages": [{"role": "user", "content": prompt}], "stream": True}
    with c.http.stream("POST", "/v1/chat/completions", json=body, timeout=None) as r:
        if r.status_code >= 400:
            r.read()
            try:
                die(r.json().get("error", {}).get("message") or r.text)
            except ValueError:
                die(r.text)
        usage = None
        for line in r.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            d = json.loads(line[6:])
            usage = d.get("usage") or usage
            for ch in d.get("choices", []):
                delta = ch.get("delta", {})
                if a.thinking and delta.get("reasoning_content"):
                    sys.stderr.write(delta["reasoning_content"])
                sys.stdout.write(delta.get("content") or "")
                sys.stdout.flush()
    print()
    if usage:
        print(f"── 입력 {usage.get('prompt_tokens')} / 출력 {usage.get('completion_tokens')} 토큰", file=sys.stderr)


# ───────────────────────── 파서 ─────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ironlab", description="공유 GPU 서버 클라이언트")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("login", help="서버 주소와 토큰 저장")
    s.add_argument("--url", required=True, help="예: https://gpubox.tailnet-xxxx.ts.net")
    s.add_argument("--token", required=True)
    s.set_defaults(fn=cmd_login, needs_client=False)

    sub.add_parser("status", help="서버·사용자 현황").set_defaults(fn=cmd_status)
    s = sub.add_parser("top", help="실시간 현황")
    s.add_argument("-n", "--interval", type=float, default=3)
    s.set_defaults(fn=cmd_top)

    s = sub.add_parser("ws", help="워크스페이스 (start|stop|info)")
    s.add_argument("action", nargs="?", default="info", choices=["start", "stop", "info"])
    s.set_defaults(fn=cmd_ws)

    s = sub.add_parser("key", help="SSH 공개키 등록")
    s.add_argument("set", choices=["set"])
    s.add_argument("file", help="예: ~/.ssh/id_ed25519.pub")
    s.set_defaults(fn=cmd_key)

    job = sub.add_parser("job", help="GPU 작업 큐").add_subparsers(dest="jcmd", required=True)
    s = job.add_parser("submit", help="작업 제출: ironlab job submit --vram 12 -- python run.py")
    s.add_argument("--vram", type=float, help="필요 VRAM(GB)")
    s.add_argument("--exclusive", action="store_true", help="GPU 독점")
    s.add_argument("--name")
    s.add_argument("--workdir", help="홈 기준 작업 폴더")
    s.add_argument("--cpus", type=float)
    s.add_argument("--mem", type=int, help="RAM(GB)")
    s.add_argument("-f", "--follow", action="store_true", help="제출 후 로그 따라가기")
    s.add_argument("command", nargs=argparse.REMAINDER, help="실행할 명령 (없으면 표준입력)")
    s.set_defaults(fn=cmd_job_submit)
    s = job.add_parser("ls", help="작업 목록")
    s.add_argument("-a", "--all", action="store_true")
    s.add_argument("-n", "--limit", type=int, default=30)
    s.set_defaults(fn=cmd_job_ls)
    s = job.add_parser("logs", help="작업 로그")
    s.add_argument("id", type=int)
    s.add_argument("-f", "--follow", action="store_true")
    s.add_argument("--tail", type=int, default=200)
    s.set_defaults(fn=cmd_job_logs)
    s = job.add_parser("cancel", help="작업 취소")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_job_cancel)

    llm = sub.add_parser("llm", help="공유 로컬 LLM").add_subparsers(dest="lcmd", required=True)
    llm.add_parser("status", help="상태와 오늘 사용량").set_defaults(fn=cmd_llm_status)
    s = llm.add_parser("load", help="모델 올리기"); s.add_argument("name"); s.set_defaults(fn=cmd_llm_load)
    llm.add_parser("unload", help="모델 내리기").set_defaults(fn=cmd_llm_unload)
    s = llm.add_parser("chat", help='질문하기: ironlab llm chat "안녕"')
    s.add_argument("--thinking", action="store_true", help="생각 과정도 표준에러로 출력")
    s.add_argument("prompt", nargs="*")
    s.set_defaults(fn=cmd_llm_chat)

    files = sub.add_parser("files", help="파일 (경로 앞에 scratch: / shared: 를 붙이면 다른 영역)").add_subparsers(dest="fcmd", required=True)
    s = files.add_parser("ls"); s.add_argument("path", nargs="?"); s.set_defaults(fn=cmd_files_ls)
    s = files.add_parser("put", help="업로드: put a.png b.png [원격폴더]")
    s.add_argument("local", nargs="+"); s.add_argument("--to", dest="dest", default="")
    s.set_defaults(fn=cmd_files_put)
    s = files.add_parser("get"); s.add_argument("remote"); s.add_argument("local", nargs="?"); s.set_defaults(fn=cmd_files_get)
    s = files.add_parser("rm"); s.add_argument("path"); s.add_argument("-r", "--recursive", action="store_true"); s.set_defaults(fn=cmd_files_rm)
    s = files.add_parser("mkdir"); s.add_argument("path"); s.set_defaults(fn=cmd_files_mkdir)
    files.add_parser("usage").set_defaults(fn=cmd_files_usage)

    admin = sub.add_parser("admin", help="관리자 명령").add_subparsers(dest="acmd", required=True)
    admin.add_parser("users", help="사용자 목록").set_defaults(fn=cmd_user_ls)
    for name, fn in (("add", cmd_user_add), ("set", cmd_user_set)):
        s = admin.add_parser(name, help=f"사용자 {'추가' if name == 'add' else '수정'}")
        s.add_argument("name")
        s.add_argument("--admin", action="store_true")
        s.add_argument("--tailscale", help="Tailscale 로그인 (예: name@github)")
        s.add_argument("--cpus", type=float)
        s.add_argument("--mem", type=int, help="RAM(GB)")
        s.add_argument("--disk", type=int, help="홈 디스크(GB)")
        s.add_argument("--max-vram", type=float)
        if name == "set":
            s.add_argument("--role", choices=["user", "admin"])
            s.add_argument("--disable", action="store_true")
            s.add_argument("--enable", action="store_true")
        s.set_defaults(fn=fn)
    s = admin.add_parser("token", help="토큰 재발급"); s.add_argument("name"); s.set_defaults(fn=cmd_user_token)
    s = admin.add_parser("events", help="이벤트 로그"); s.add_argument("-n", "--limit", type=int, default=50); s.set_defaults(fn=cmd_events)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if getattr(args, "command", None) and args.command[:1] == ["--"]:
        args.command = args.command[1:]
    client = None if getattr(args, "needs_client", True) is False else Client(load_cfg())
    args.fn(args, client)


if __name__ == "__main__":
    main()
