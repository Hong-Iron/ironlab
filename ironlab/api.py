"""FastAPI 앱: REST API + 웹 대시보드."""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import files as fs
from . import flow, gen
from . import users as um
from .config import Settings, load_settings
from .db import DB
from .gpu import make_gpu
from .llm import LLMError, LLMService
from .metrics import render_prometheus
from .monitor import Monitor
from .runtime import make_runtime
from .scheduler import JobError, Scheduler

WEB_DIR = Path(__file__).parent / "web"


class Ctx:
    """앱 전역 구성요소 묶음."""

    def workspace_op(self, username: str, fn, *args):
        """한 사용자의 워크스페이스 시작·중지를 한 번에 하나씩만 한다. 버튼을 연달아 누르거나 탭 두 개에서
        동시에 누르면 같은 이름의 컨테이너를 두 번 만들려다 한쪽이 오류가 난다."""
        with self._ws_guard:
            lock = self._ws_locks.setdefault(username, threading.Lock())
        with lock:
            return fn(*args)

    def __init__(self, settings: Settings):
        settings.ensure_dirs()
        self.s = settings
        self._ws_guard = threading.Lock()
        self._ws_locks: dict[str, threading.Lock] = {}
        if not settings.mock:
            # 업로드는 일단 임시 파일로 받는다. 기본 위치 /tmp 는 메모리(tmpfs)라 큰 모델 파일을 올리면 RAM 이 찬다.
            # 사용자 파일과 같은 큰 디스크에 둔다 (사용자 이름은 영문자로 시작하므로 이 이름과 겹치지 않는다).
            spool = settings.scratch_root / ".upload-tmp"
            spool.mkdir(mode=0o700, exist_ok=True)
            tempfile.tempdir = str(spool)
        self.db = DB(settings.db_path)
        try:
            healed = um.sync_authorized_keys(self.db, settings)
            if healed:
                log.warning("SSH 키 파일 %d개가 등록된 키와 달라 다시 썼습니다", healed)
        except Exception:
            log.exception("SSH 키 파일을 맞추지 못했습니다")
        self.rt = make_runtime(settings)
        self.gpu = make_gpu(settings, self.rt)
        self.sched = Scheduler(self.db, settings, self.rt, self.gpu.total_gb)
        self.monitor = Monitor(self.db, settings, self.rt, self.gpu, self.sched)
        try:
            self.rt.ensure_network()
        except Exception as e:  # Docker 가 아직 안 떠 있어도 콘솔은 뜨게 한다
            logging.getLogger("ironlab").warning("ironlab 네트워크 준비 실패: %s", e)
        self.llm = LLMService(self.db, settings, self.rt, self.sched)
        self.monitor.llm = self.llm
        self.monitor.after_tick.append(self._advance_flows)

    def _advance_flows(self) -> None:
        """노드 편집기의 실행을 한 걸음 나아가게 한다. 앞 노드가 끝나 새 작업을 큐에 넣었으면,
        다음 주기(monitor_interval_s)를 기다리지 않고 바로 큐를 다시 돌리게 한다."""
        if flow.advance(self.db, self.sched, self.s):
            self.monitor.request_full()


# ── 요청 본문 ──
class JobIn(BaseModel):
    command: str = Field(min_length=1, max_length=8000)
    vram_gb: float = Field(gt=0)
    name: str = ""
    cpus: float | None = Field(default=None, gt=0)
    mem_gb: int | None = Field(default=None, gt=0)
    exclusive: bool = False
    workdir: str = ""


class KeysIn(BaseModel):
    keys: str


class PresetIn(BaseModel):
    name: str
    params: dict


class JoinIn(BaseModel):
    ids: list[int]


class FlowIn(BaseModel):
    name: str = ""
    graph: dict


class MkdirIn(BaseModel):
    area: str = "home"
    path: str = ""
    name: str


class UserIn(BaseModel):
    name: str
    role: str = "user"
    tailscale_login: str | None = None
    cpus: float | None = Field(default=None, gt=0, le=1024)
    mem_gb: int | None = Field(default=None, gt=0, le=4096)
    disk_gb: int | None = Field(default=None, gt=0)
    max_vram_gb: float | None = Field(default=None, gt=0)


class LLMModelIn(BaseModel):
    name: str
    file: str
    vram_gb: float = Field(gt=0)
    mmproj: str = ""
    ctx: int = 32768
    parallel: int = 2
    extra_args: str = ""


class LLMModelPatch(BaseModel):
    """등록한 모델에서 바꿀 값만 보낸다 (이름은 바꿀 수 없다)."""
    file: str | None = None
    vram_gb: float | None = Field(default=None, gt=0)
    mmproj: str | None = None       # 빈 문자열이면 이미지 입력을 끈다
    ctx: int | None = None
    parallel: int | None = None
    extra_args: str | None = None   # 빈 문자열이면 추가 인자를 지운다


class LLMLoadIn(BaseModel):
    name: str


class UserPatch(BaseModel):
    role: str | None = None
    tailscale_login: str | None = None      # 빈 문자열이면 연결을 끊는다
    cpus: float | None = Field(default=None, gt=0, le=1024)
    mem_gb: int | None = Field(default=None, gt=0, le=4096)
    disk_gb: int | None = Field(default=None, gt=0)
    max_vram_gb: float | None = Field(default=None, ge=0)      # 0이면 한도 없음
    disabled: bool | None = None


def stuck_hint(e: Exception) -> str:
    """Docker 가 컨테이너를 죽이지 못했을 때, 무엇을 해야 하는지 덧붙인다."""
    msg = str(e)
    if "did not receive an exit event" in msg or "could not kill container" in msg:
        return ("컨테이너 안에 종료되지 않는 프로세스가 있어 Docker 가 컨테이너를 정리하지 못했습니다. "
                "관리자가 서버를 재부팅해야 풀립니다. (" + msg[-160:] + ")")
    if "is already in use by container" in msg:
        # runtime 이 이름을 쥔 컨테이너를 지워 보고 예비 이름까지 써 본 뒤에도 실패한 경우에만 여기로 온다
        return ("Docker 가 이 컨테이너 이름을 쥔 채 놓지 않고(목록에는 없는 컨테이너), 예비 이름으로도 띄우지 못했습니다. "
                "관리자가 서버에서 Docker 를 다시 시작하거나 서버를 재부팅해야 풀립니다. (" + msg[-160:] + ")")
    if "port is already allocated" in msg or "address already in use" in msg:
        return ("컨테이너가 쓸 포트를 다른 프로세스가 이미 쓰고 있습니다. 정리되지 않은 이전 컨테이너가 남아 있을 수 있습니다. "
                "관리자가 서버에서 'sudo ss -ltnp' 로 그 포트를 쥔 프로세스를 확인하세요. (" + msg[-160:] + ")")
    return msg


log = logging.getLogger("ironlab")

LOOPBACK = ("127.0.0.1", "::1")
# 프록시(tailscale serve)를 거친 요청에 붙는 헤더. 이게 있으면 주소가 127.0.0.1 이어도 '이 기계에서 온 요청'이 아니다.
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "tailscale-user-login")


def cross_site(request: Request, public_host: str) -> bool:
    """브라우저가 다른 사이트의 페이지에서 보낸 요청인지.

    Tailscale 로그인은 쿠키처럼 브라우저가 알아서 따라붙는 신원이라, 사용자가 tailnet 안에서 아무 웹페이지나
    열면 그 페이지가 이 서버로 요청을 보낼 수 있다 (CSRF). 브라우저가 붙이는 Sec-Fetch-Site / Origin 으로 가려낸다.
    CLI 같은 브라우저 아닌 클라이언트는 이 헤더들을 보내지 않으므로 영향이 없다.
    """
    site = request.headers.get("sec-fetch-site")
    if site:
        return site not in ("same-origin", "none")
    origin = request.headers.get("origin")
    if not origin:
        return False
    host = (urlsplit(origin).netloc or "").lower()
    own = {h.lower() for h in (request.headers.get("host"), request.headers.get("x-forwarded-host"), public_host) if h}
    return host not in own


def create_app(settings: Settings | None = None, start_monitor: bool = True) -> FastAPI:
    ctx = Ctx(settings or load_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_monitor:
            ctx.monitor.start()
        yield
        if start_monitor:
            ctx.monitor.stop()

    app = FastAPI(title="ironlab", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.ctx = ctx

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        """처리하지 못한 오류도 이유를 담아 돌려준다 (빈 '500 Internal Server Error' 로는 원인을 알 수 없다)."""
        backend = type(exc).__module__.split(".")[0] in ("docker", "requests", "urllib3")
        detail = (f"Docker 가 응답하지 않거나 요청을 처리하지 못했습니다: {stuck_hint(exc)}" if backend
                  else f"서버 내부 오류 ({type(exc).__name__}): {exc}")
        return JSONResponse({"detail": detail[:400]}, status_code=502 if backend else 500)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and cross_site(request, ctx.s.public_host):
            return JSONResponse({"detail": "다른 사이트에서 보낸 요청은 받지 않습니다"}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Frame-Options", "DENY")              # 다른 사이트가 이 화면을 프레임에 넣어 클릭을 유도하지 못하게
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            # 업데이트 뒤 예전 화면 코드가 남지 않게, 쓸 때마다 바뀌었는지 확인하게 한다 (안 바뀌었으면 304)
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    # ── 인증 ──
    def current_user(request: Request) -> dict:
        user = None
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        if token:
            user = ctx.db.one("SELECT * FROM users WHERE token_hash=?", (um.hash_token(token),))
        elif ctx.s.trust_tailscale_headers and request.client and request.client.host in LOOPBACK:
            # 이 헤더는 같은 기계의 tailscale serve 만 붙일 수 있어야 한다. 다른 곳에서 온 요청의 헤더는 믿지 않는다.
            login = request.headers.get("tailscale-user-login")
            if login:
                user = ctx.db.one("SELECT * FROM users WHERE tailscale_login=?", (login,))
        if user is None:
            raise HTTPException(401, "로그인이 필요합니다 (토큰 또는 등록된 Tailscale 계정)")
        if user["disabled"]:
            raise HTTPException(403, "비활성화된 계정입니다")
        now = time.time()
        if not user["last_seen"] or now - user["last_seen"] > 30:
            ctx.db.execute("UPDATE users SET last_seen=? WHERE name=?", (now, user["name"]))
        return user

    def admin_user(user: dict = Depends(current_user)) -> dict:
        if user["role"] != "admin":
            raise HTTPException(403, "관리자만 할 수 있습니다")
        return user

    def bad_request(e: Exception):
        raise HTTPException(400, str(e))

    # ── 기본 ──
    @app.get("/api/health")
    def health():
        try:
            gpu = bool(ctx.gpu.snapshot().get("available", False))
        except Exception:
            gpu = False
        snap = ctx.monitor.latest
        return {"ok": True, "mock": ctx.s.mock, "gpu": gpu,
                "monitor_age_s": round(time.time() - snap["ts"], 1) if snap else None,   # 수집이 멈췄는지 밖에서 볼 수 있게
                "backend_error": ctx.monitor.backend_error}

    @app.get("/api/site")
    def site():
        """로그인 전에도 보이는 서버 이름 (로그인 화면·브라우저 탭)."""
        return {"name": ctx.s.site_name or "ironlab", "tailscale": ctx.s.trust_tailscale_headers}

    @app.get("/api/me")
    def me(user: dict = Depends(current_user)):
        return um.public_view(user, ctx.s) | {"public_host": ctx.s.public_host, "public_url": ctx.s.console_url,
                                              "site_name": ctx.s.site_name or "ironlab"}

    @app.put("/api/me/ssh-keys")
    def set_keys(body: KeysIn, user: dict = Depends(current_user)):
        try:
            keys = um.validate_pubkeys(body.keys)
        except um.UserError as e:
            bad_request(e)
        ctx.db.execute("UPDATE users SET ssh_pubkeys=? WHERE name=?", (keys, user["name"]))
        um.write_authorized_keys(ctx.s, user["name"], keys)
        ctx.db.log_event("user.ssh_keys", user["name"], f"{len(keys.splitlines())} keys")
        return {"ok": True, "count": len(keys.splitlines())}

    # ── 모니터링 ──
    @app.get("/api/overview")
    def overview(user: dict = Depends(current_user)):
        ctx.monitor.touch()     # 누군가 보고 있는 동안에는 1초 주기로 수치를 갱신한다
        if not ctx.monitor.latest:
            ctx.monitor.collect()
        snap = ctx.monitor.latest
        # 수치가 얼마나 오래됐는지 서버 시계로 계산해 준다 (수집이 멈추면 화면이 그대로 굳기 때문에 알려야 한다)
        return snap | {"age_s": round(time.time() - snap["ts"], 1)}

    @app.get("/api/history")
    def history(res: str = Query("slow", pattern="^(slow|fast)$"), user: dict = Depends(current_user)):
        """slow: 전체 수집 주기(기본 10초)로 쌓은 긴 기록. fast: 가벼운 갱신까지 포함한 최근 몇 분."""
        return list(ctx.monitor.recent if res == "fast" else ctx.monitor.history)

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(request: Request):
        # 서버에서 직접 긁어 가는 Prometheus 만 로그인 없이 허용한다.
        # tailscale serve 를 거친 요청도 주소는 127.0.0.1 이므로, 프록시 헤더가 있으면 로컬로 보지 않는다.
        local = (request.client and request.client.host in LOOPBACK
                 and not any(h in request.headers for h in PROXY_HEADERS))
        if not local:
            admin_user(current_user(request))
        return render_prometheus(ctx.monitor.latest, ctx.db)

    # ── 워크스페이스 ──
    def ws_view(user: dict) -> dict:
        info = ctx.rt.get_workspace(user["name"])
        port = um.ssh_port(ctx.s, user)
        host = ctx.s.public_host or "<서버주소>"
        return {
            "state": info["state"] if info else "stopped",
            "started_at": info["started_at"] if info else None,
            "ssh_port": port,
            "ssh_command": f"ssh -p {port} dev@{host}",
            "has_ssh_key": bool(user["ssh_pubkeys"]),
            "limits": {"cpus": user["cpus"], "mem_gb": user["mem_gb"]},
        }

    @app.get("/api/workspace")
    def ws_get(user: dict = Depends(current_user)):
        return ws_view(user)

    @app.post("/api/workspace/start")
    async def ws_start(user: dict = Depends(current_user)):
        try:
            await run_in_threadpool(ctx.workspace_op, user["name"], ctx.rt.start_workspace, user)
        except Exception as e:
            raise HTTPException(502, f"워크스페이스 시작 실패: {stuck_hint(e)}")
        ctx.monitor.request_full()
        ctx.db.log_event("workspace.start", user["name"])
        return ws_view(user)

    @app.post("/api/workspace/stop")
    async def ws_stop(user: dict = Depends(current_user)):
        try:
            await run_in_threadpool(ctx.workspace_op, user["name"], ctx.rt.stop_workspace, user["name"])
        except Exception as e:
            raise HTTPException(502, f"워크스페이스 중지 실패: {stuck_hint(e)}")
        ctx.monitor.request_full()
        ctx.db.log_event("workspace.stop", user["name"])
        return ws_view(user)

    # ── GPU 작업 ──
    def job_view(job: dict, viewer: dict) -> dict:
        mine = job["user"] == viewer["name"] or viewer["role"] == "admin"
        out = dict(job)
        out["wait_reason"] = ctx.sched.reasons.get(job["id"]) if job["status"] == "queued" else None
        if not mine:
            out["command"] = None
            out["workdir"] = None
        out.pop("container_id", None)
        return out

    @app.get("/api/jobs")
    def jobs_list(scope: str = "mine", limit: int = Query(100, ge=1, le=500),
                  user: dict = Depends(current_user)):
        if scope == "all":
            rows = ctx.db.all("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = ctx.db.all("SELECT * FROM jobs WHERE user=? ORDER BY id DESC LIMIT ?",
                              (user["name"], limit))
        return [job_view(j, user) for j in rows]

    @app.post("/api/jobs")
    def jobs_submit(body: JobIn, user: dict = Depends(current_user)):
        try:
            job = ctx.sched.submit(user, **body.model_dump())
        except JobError as e:
            bad_request(e)
        return job_view(job, user)

    def own_job(job_id: int, user: dict) -> dict:
        job = ctx.sched.get(job_id)
        if job is None:
            raise HTTPException(404, "없는 작업")
        if job["user"] != user["name"] and user["role"] != "admin":
            raise HTTPException(403, "다른 사람의 작업입니다")
        return job

    @app.get("/api/jobs/{job_id}")
    def jobs_get(job_id: int, user: dict = Depends(current_user)):
        return job_view(own_job(job_id, user), user)

    @app.get("/api/jobs/{job_id}/logs", response_class=PlainTextResponse)
    async def jobs_logs(job_id: int, tail: int = Query(200, ge=0, le=100000),
                        user: dict = Depends(current_user)):
        job = own_job(job_id, user)
        return await run_in_threadpool(ctx.sched.logs, job, tail or None)

    @app.post("/api/jobs/{job_id}/cancel")
    async def jobs_cancel(job_id: int, user: dict = Depends(current_user)):
        own_job(job_id, user)
        try:
            job = await run_in_threadpool(ctx.sched.cancel, job_id, user["name"])
        except JobError as e:
            bad_request(e)
        return job_view(job, user)

    # ── 파일 ──
    def area_check(area: str) -> str:
        if area not in fs.AREAS:
            raise HTTPException(400, f"area는 {', '.join(fs.AREAS)} 중 하나")
        return area

    @app.get("/api/files")
    def files_list(area: str = "home", path: str = "", user: dict = Depends(current_user)):
        root, writable = fs.area_root(ctx.s, user, area_check(area))
        try:
            data = fs.list_dir(root, path, strict=area != "shared")
        except fs.FileError as e:
            bad_request(e)
        return data | {"area": area, "writable": writable}

    @app.get("/api/files/usage")
    async def files_usage(user: dict = Depends(current_user)):
        return await run_in_threadpool(fs.quota_info, ctx.s, user)

    @app.get("/api/files/download")
    def files_download(area: str, path: str, user: dict = Depends(current_user)):
        try:
            f, name, size = fs.open_for_download(ctx.s, user, area_check(area), path)
        except fs.FileError as e:
            bad_request(e)

        def chunks():
            with f:
                while block := f.read(fs.CHUNK):
                    yield block
        # 검사한 그 파일을 연 채로 내보낸다. 사용자가 올린 HTML 등이 브라우저에서 실행되지 않도록 항상 첨부로 보낸다
        return StreamingResponse(chunks(), media_type="application/octet-stream", headers={
            "Content-Length": str(size),
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        })

    @app.post("/api/files/upload")
    async def files_upload(area: str = Form("home"), path: str = Form(""),
                           file: UploadFile = File(...), user: dict = Depends(current_user)):
        try:
            res = await run_in_threadpool(fs.save_upload, ctx.s, user, area_check(area), path,
                                          file.filename or "upload", file.file)
        except fs.QuotaExceeded as e:
            raise HTTPException(413, str(e))
        except fs.FileError as e:
            bad_request(e)
        return res

    @app.post("/api/files/mkdir")
    def files_mkdir(body: MkdirIn, user: dict = Depends(current_user)):
        try:
            fs.make_dir(ctx.s, user, area_check(body.area), body.path, body.name)
        except fs.FileError as e:
            bad_request(e)
        return {"ok": True}

    @app.delete("/api/files")
    async def files_delete(area: str, path: str, recursive: bool = False,
                           user: dict = Depends(current_user)):
        try:
            await run_in_threadpool(fs.delete_path, ctx.s, user, area_check(area), path, recursive)
        except fs.FileError as e:
            bad_request(e)
        return {"ok": True}


    # ── 생성 (3D, 이미지) ──
    @app.get("/api/gen/{kind}")
    async def gen_state(kind: str, user: dict = Depends(current_user)):
        try:
            return await run_in_threadpool(gen.state, ctx.db, ctx.sched, ctx.s, user, kind)
        except gen.GenError as e:
            bad_request(e)

    @app.post("/api/gen/{kind}")
    async def gen_submit(kind: str, files: list[UploadFile] | None = File(None), texts: str = Form("{}"),
                         params: str = Form("{}"), roles: str = Form("[]"), thumb: UploadFile | None = File(None),
                         after: str = Form(""), refs: list[UploadFile] | None = File(None),
                         user: dict = Depends(current_user)):
        try:
            parsed, words, picked = json.loads(params or "{}"), json.loads(texts or "{}"), json.loads(roles or "[]")
        except ValueError:
            raise HTTPException(400, f"설정(params), 글(texts), 방향(roles) 가운데 JSON 이 아닌 것이 있습니다 "
                                     f"(받은 값 {params[:60]!r}, {texts[:60]!r}, {roles[:60]!r})")
        if after.strip() and not after.strip().isdigit():
            raise HTTPException(400, f"이어 만들 앞 영상 번호(after)는 숫자여야 합니다 (받은 값 {after[:20]!r})")
        prev = int(after) if after.strip() else None
        try:
            return await run_in_threadpool(
                lambda: gen.submit(ctx.db, ctx.sched, ctx.s, user, kind,
                                   files=[(f.filename or "", f.file) for f in files or []], texts=words,
                                   params=parsed, thumb=thumb.file if thumb else None, roles=picked, after=prev,
                                   refs=[(f.filename or "", f.file) for f in refs or []]))
        except fs.QuotaExceeded as e:
            raise HTTPException(413, str(e))
        except (gen.GenError, fs.FileError, JobError) as e:
            bad_request(e)

    @app.post("/api/gen/{kind}/join")
    async def gen_join(kind: str, body: JoinIn, user: dict = Depends(current_user)):
        if kind != "video":
            raise HTTPException(404, f"이어 붙이기는 영상에만 있습니다 (받은 종류 {kind!r})")
        try:
            return await run_in_threadpool(gen.join_videos, ctx.db, ctx.sched, ctx.s, user, body.ids)
        except (gen.GenError, fs.FileError, JobError) as e:
            bad_request(e)

    @app.post("/api/gen/{kind}/presets")
    def gen_preset_save(kind: str, body: PresetIn, user: dict = Depends(current_user)):
        try:
            return gen.save_preset(ctx.db, user, kind, body.name, body.params)
        except gen.GenError as e:
            bad_request(e)

    @app.delete("/api/gen/{kind}/presets/{preset_id}")
    def gen_preset_delete(kind: str, preset_id: int, user: dict = Depends(current_user)):
        try:
            gen.delete_preset(ctx.db, user, kind, preset_id)
        except gen.GenError as e:
            bad_request(e)
        return {"ok": True}

    @app.delete("/api/gen/{kind}/{run_id}")
    async def gen_delete(kind: str, run_id: int, user: dict = Depends(current_user)):
        try:
            await run_in_threadpool(gen.delete, ctx.db, ctx.sched, ctx.s, user, kind, run_id)
        except gen.GenError as e:
            bad_request(e)
        return {"ok": True}

    # ── 노드 편집기 ──
    FLOW_ERRORS = (flow.FlowError, gen.GenError, fs.FileError, JobError)

    @app.get("/api/flow")
    async def flow_state(user: dict = Depends(current_user)):
        return await run_in_threadpool(flow.state, ctx.db, ctx.s, user)

    @app.post("/api/flow/flows")
    def flow_save(body: FlowIn, user: dict = Depends(current_user)):
        try:
            return flow.save_flow(ctx.db, user, body.name, body.graph)
        except FLOW_ERRORS as e:
            bad_request(e)

    @app.delete("/api/flow/flows/{flow_id}")
    def flow_delete(flow_id: int, user: dict = Depends(current_user)):
        try:
            flow.delete_flow(ctx.db, user, flow_id)
        except FLOW_ERRORS as e:
            bad_request(e)
        return {"ok": True}

    @app.post("/api/flow/upload")
    async def flow_upload(file: UploadFile = File(...), user: dict = Depends(current_user)):
        try:
            return await run_in_threadpool(flow.save_input, ctx.s, user, file.filename or "", file.file)
        except fs.QuotaExceeded as e:
            raise HTTPException(413, str(e))
        except FLOW_ERRORS as e:
            bad_request(e)

    @app.post("/api/flow/runs")
    async def flow_run(body: FlowIn, user: dict = Depends(current_user)):
        try:
            run = await run_in_threadpool(flow.start_run, ctx.db, ctx.sched, ctx.s, user, body.graph, body.name)
        except fs.QuotaExceeded as e:
            raise HTTPException(413, str(e))
        except FLOW_ERRORS as e:
            bad_request(e)
        ctx.monitor.request_full()                      # 방금 넣은 작업을 다음 주기를 기다리지 않고 시작하게 한다
        return run

    @app.get("/api/flow/runs/{run_id}")
    async def flow_run_detail(run_id: int, user: dict = Depends(current_user)):
        try:
            return await run_in_threadpool(flow.run_detail, ctx.db, ctx.sched, ctx.s, user, run_id)
        except FLOW_ERRORS as e:
            bad_request(e)

    @app.post("/api/flow/runs/{run_id}/cancel")
    async def flow_run_cancel(run_id: int, user: dict = Depends(current_user)):
        try:
            return await run_in_threadpool(flow.cancel_run, ctx.db, ctx.sched, ctx.s, user, run_id)
        except FLOW_ERRORS as e:
            bad_request(e)

    @app.delete("/api/flow/runs/{run_id}")
    async def flow_run_delete(run_id: int, user: dict = Depends(current_user)):
        try:
            await run_in_threadpool(flow.delete_run, ctx.db, ctx.s, user, run_id)
        except FLOW_ERRORS as e:
            bad_request(e)
        return {"ok": True}


    # ── 로컬 LLM ──
    @app.get("/api/llm")
    def llm_status(user: dict = Depends(current_user)):
        day = time.time() - 86400
        base = ctx.s.console_url or "https://<서버주소>"
        return ctx.llm.status() | {
            "models": ctx.llm.models(),
            "usage_today": ctx.llm.usage(day),
            "usage_week": ctx.llm.usage(time.time() - 7 * 86400),
            "budget_gb": ctx.sched.budget_gb,
            "endpoints": {"external": f"{base}/v1",
                          "internal": f"http://{ctx.s.network_gateway}:{ctx.s.internal_port}/v1"},
        }

    @app.get("/api/llm/files")
    def llm_files(admin: dict = Depends(admin_user)):
        return ctx.llm.gguf_files()

    @app.post("/api/llm/models")
    def llm_register(body: LLMModelIn, admin: dict = Depends(admin_user)):
        try:
            return ctx.llm.register(**body.model_dump())
        except LLMError as e:
            bad_request(e)

    @app.patch("/api/llm/models/{name}")
    def llm_update(name: str, body: LLMModelPatch, admin: dict = Depends(admin_user)):
        try:
            return ctx.llm.update(name, by=admin["name"], **body.model_dump())
        except LLMError as e:
            bad_request(e)

    @app.delete("/api/llm/models/{name}")
    def llm_delete(name: str, admin: dict = Depends(admin_user)):
        try:
            ctx.llm.delete(name)
        except LLMError as e:
            bad_request(e)
        return {"ok": True}

    @app.post("/api/llm/load")
    async def llm_load(body: LLMLoadIn, user: dict = Depends(current_user)):
        try:
            return await run_in_threadpool(ctx.llm.load, body.name, user["name"])
        except LLMError as e:
            raise HTTPException(409, str(e))
        except Exception as e:
            raise HTTPException(502, f"llama-server 시작 실패: {stuck_hint(e)}")
        finally:
            ctx.monitor.request_full()      # 다음 주기를 기다리지 않고 화면에 반영한다

    @app.post("/api/llm/unload")
    async def llm_unload(user: dict = Depends(current_user)):
        st = ctx.llm.status()
        if user["role"] != "admin" and st.get("loaded_by") != user["name"]:
            raise HTTPException(403, "모델을 올린 사람이나 관리자만 내릴 수 있습니다")
        await run_in_threadpool(ctx.llm.unload, user["name"], "직접 내림")
        return ctx.llm.status()

    @app.api_route("/v1/{path:path}", methods=["GET", "POST"])
    async def llm_proxy(path: str, request: Request, user: dict = Depends(current_user)):
        source = "console" if request.headers.get("x-ironlab-source") == "console" else "api"
        return await ctx.llm.proxy(path, request.method, await request.body(), user["name"], source)

    # ── 관리자 ──
    @app.get("/api/admin/users")
    def admin_users(admin: dict = Depends(admin_user)):
        return [um.public_view(u, ctx.s) for u in ctx.db.list_users()]

    @app.post("/api/admin/users")
    def admin_create(body: UserIn, admin: dict = Depends(admin_user)):
        try:
            user, token = um.create_user(ctx.db, ctx.s, **body.model_dump())
        except um.UserError as e:
            bad_request(e)
        return um.public_view(user, ctx.s) | {"token": token}

    @app.patch("/api/admin/users/{name}")
    def admin_patch(name: str, body: UserPatch, admin: dict = Depends(admin_user)):
        try:
            user = um.update_user(ctx.db, name, **body.model_dump())
        except um.UserError as e:
            bad_request(e)
        if body.disabled:
            try:
                ctx.workspace_op(name, ctx.rt.stop_workspace, name)
            except Exception as e:
                raise HTTPException(502, f"계정은 비활성화했지만 워크스페이스를 중지하지 못했습니다: {stuck_hint(e)}") from e
        return um.public_view(user, ctx.s)

    @app.post("/api/admin/users/{name}/token")
    def admin_token(name: str, admin: dict = Depends(admin_user)):
        if not ctx.db.get_user(name):
            raise HTTPException(404, "없는 사용자")
        return {"name": name, "token": um.rotate_token(ctx.db, name)}

    @app.post("/api/admin/workspaces/{name}/stop")
    async def admin_ws_stop(name: str, admin: dict = Depends(admin_user)):
        try:
            stopped = await run_in_threadpool(ctx.workspace_op, name, ctx.rt.stop_workspace, name)
        except Exception as e:
            raise HTTPException(502, f"워크스페이스 중지 실패: {stuck_hint(e)}")
        ctx.db.log_event("workspace.stop", name, f"by {admin['name']}")
        return {"stopped": stopped}

    @app.get("/api/admin/events")
    def admin_events(limit: int = Query(100, ge=1, le=1000), admin: dict = Depends(admin_user)):
        return ctx.db.recent_events(limit)

    # ── 웹 ──
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")

    return app


def create_internal_app(ctx: Ctx) -> FastAPI:
    """워크스페이스·작업 컨테이너 전용 LLM 프록시. ironlab 네트워크 게이트웨이 IP 에만 열린다.
    토큰 대신 요청을 보낸 컨테이너의 IP 로 사용자를 알아본다."""
    app = FastAPI(title="ironlab-internal", docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/v1/{path:path}", methods=["GET", "POST"])
    async def proxy(path: str, request: Request):
        ip = request.client.host if request.client else None
        user = ctx.llm.user_for_ip(ip)
        if user is None:
            ctx.llm.ip_users = await run_in_threadpool(ctx.rt.ip_map)  # 방금 뜬 컨테이너일 수 있다
            user = ctx.llm.user_for_ip(ip)
        if user is None:
            raise HTTPException(403, f"ironlab 컨테이너가 아닌 곳의 요청입니다 ({ip})")
        return await ctx.llm.proxy(path, request.method, await request.body(), user, "workspace")

    @app.get("/health")
    def health():
        return {"ok": True}

    return app
