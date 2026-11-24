"""공유 로컬 LLM: llama.cpp 서버 하나를 모두가 나눠 쓴다.

- 모델 올리기/내리기: 올릴 때 GPU 큐의 VRAM 여유를 확인하고, 일정 시간 안 쓰면 자동으로 내린다.
- OpenAI 호환 /v1 프록시: 누가 얼마나 썼는지 기록한다.
  · 노트북: ironlab 토큰 또는 Tailscale 계정으로 인증
  · 워크스페이스·작업 컨테이너: ironlab 네트워크 위 컨테이너 IP 로 사용자를 알아본다 (키 불필요)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import threading
import time

import httpx
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .config import Settings
from .db import DB

log = logging.getLogger("ironlab.llm")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
EDITABLE = ("file", "mmproj", "ctx", "parallel", "vram_gb", "extra_args")
# 이 값들이 바뀌면 GPU 메모리 사용량이 달라지므로, 전에 잰 값은 버린다
AFFECTS_VRAM = ("file", "mmproj", "ctx", "parallel", "extra_args")


def per_request_ctx(model: dict) -> int:
    """요청 하나가 쓸 수 있는 컨텍스트(토큰). llama-server 는 전체 컨텍스트를 동시 요청 수로 똑같이 나눈다
    (예: 32,768 토큰에 동시 요청 2개면 요청당 16,384 토큰)."""
    return int(model["ctx"]) // max(1, int(model["parallel"]))


class LLMError(ValueError):
    pass


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": {"message": msg, "type": "ironlab_error"}}, status_code=status)


class LLMService:
    def __init__(self, db: DB, settings: Settings, runtime, scheduler):
        self.db, self.s, self.rt, self.sched = db, settings, runtime, scheduler
        self.state = "off"          # off | loading | ready | error
        self.error: str | None = None
        self.loaded_at: float | None = None
        self.last_used: float | None = None
        self.ip_users: dict[str, dict] = {}
        self.metrics: dict = {}
        self._lock = threading.RLock()
        self._client: httpx.AsyncClient | None = None
        self._active: dict | None = None
        self._active_at = 0.0
        self._active_known = False
        self.vram_used_gb: float | None = None      # llama-server 가 지금 실제로 쓰는 GPU 메모리
        self._sync_state()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.s.llm_port}"

    # ── 모델 등록 ──
    def models(self) -> list[dict]:
        return self.db.all("SELECT * FROM llm_models ORDER BY name")

    def model(self, name: str) -> dict | None:
        return self.db.one("SELECT * FROM llm_models WHERE name=?", (name,))

    def gguf_files(self) -> list[dict]:
        root = self.s.llm_models_dir
        if not root.exists():
            return []
        out = []
        for p in sorted(root.rglob("*.gguf")):
            rel = str(p.relative_to(root))
            out.append({"path": rel, "size": p.stat().st_size, "mmproj": "mmproj" in p.name.lower()})
        return out

    def _check_file(self, rel: str) -> str:
        rel = rel.strip().lstrip("/")
        if not rel:
            return ""
        root = self.s.llm_models_dir.resolve()
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            raise LLMError("모델 파일은 /srv/shared/models/llm 안에 있어야 합니다")
        if not target.is_file() and not self.s.mock:
            raise LLMError(f"파일이 없습니다: {rel}")
        return str(target.relative_to(root))

    def register(self, *, name: str, file: str, vram_gb: float, mmproj: str = "", ctx: int = 32768,
                 parallel: int = 2, extra_args: str = "") -> dict:
        if not NAME_RE.match(name):
            raise LLMError("이름은 영문 소문자·숫자로 시작하는 64자 이내 (a-z 0-9 . _ -)")
        if self.model(name):
            raise LLMError(f"이미 등록된 이름입니다: {name}")
        self._check_numbers(vram_gb, ctx, parallel)
        f = self._check_file(file)
        if not f:
            raise LLMError("GGUF 파일을 고르세요")
        mm = self._check_file(mmproj) if mmproj else ""
        self.db.execute("""INSERT INTO llm_models(name, file, mmproj, ctx, parallel, vram_gb, extra_args, created_at)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (name, f, mm, int(ctx), int(parallel), float(vram_gb), self._check_args(extra_args),
                         time.time()))
        self.db.log_event("llm.register", None, f"{name} ({f}, {vram_gb:g}GB)")
        return self.model(name)

    def _check_numbers(self, vram_gb: float, ctx: int, parallel: int) -> None:
        total = getattr(self.sched, "total_gb", 0) or 1024    # GPU 전체 용량보다 큰 예약은 결코 올라가지 못한다
        if not (0 < vram_gb <= total):
            raise LLMError(f"VRAM 예약은 0보다 크고 GPU 전체({total:g} GB) 이하여야 합니다 (받은 값 {vram_gb:g} GB)")
        if not (512 <= ctx <= 1_048_576):
            raise LLMError(f"컨텍스트는 512 ~ 1,048,576 토큰이어야 합니다 (받은 값 {ctx:,} 토큰)")
        if not (1 <= parallel <= 32):
            raise LLMError(f"동시 요청 수는 1 ~ 32 여야 합니다 (받은 값 {parallel})")

    @staticmethod
    def _check_args(extra_args: str) -> str:
        """추가 llama-server 인자. 따옴표가 안 닫힌 채로 저장되면 모델을 올릴 때에야 오류가 난다."""
        text = (extra_args or "").strip()
        try:
            shlex.split(text)
        except ValueError as e:
            raise LLMError(f"추가 인자를 읽을 수 없습니다 ({e}). 따옴표가 짝이 맞는지 확인하세요: {text}") from e
        return text

    def update(self, name: str, by: str | None = None, **changes) -> dict:
        """등록한 모델의 설정(컨텍스트, 동시 요청 수, VRAM 예약 등)을 고친다.

        올라가 있는 모델은 고칠 수 없다: 떠 있는 llama-server 는 옛 설정으로 돌고 있어, 화면에 보이는 값과
        실제가 어긋난다. 내린 뒤 고치고 다시 올린다.
        """
        with self._lock:
            model = self.model(name)
            if model is None:
                raise LLMError(f"등록되지 않은 모델: {name}")
            info = self.rt.get_llm()
            if info and info.get("model") == name and info["state"] == "running":
                raise LLMError("올라가 있는 모델은 고칠 수 없습니다. 먼저 내린 뒤 고치고 다시 올려 주세요")
            changes = {k: v for k, v in changes.items() if k in EDITABLE and v is not None}
            if "file" in changes:
                changes["file"] = self._check_file(changes["file"])
                if not changes["file"]:
                    raise LLMError("GGUF 파일을 고르세요")
            if "mmproj" in changes:
                changes["mmproj"] = self._check_file(changes["mmproj"]) if changes["mmproj"] else ""
            if "extra_args" in changes:
                changes["extra_args"] = self._check_args(changes["extra_args"])
            changes = {k: v for k, v in changes.items() if v != model[k]}
            new = model | changes
            self._check_numbers(new["vram_gb"], new["ctx"], new["parallel"])
            if not changes:
                return model
            detail = ", ".join(f"{k}: {model[k]} → {v}" for k, v in changes.items())
            if any(k in changes for k in AFFECTS_VRAM):
                changes["measured_vram_gb"] = None
            cols = ", ".join(f"{k}=?" for k in changes)     # 열 이름은 EDITABLE 과 measured_vram_gb 뿐이다
            self.db.execute(f"UPDATE llm_models SET {cols} WHERE name=?", (*changes.values(), name))  # noqa: S608
            self.db.log_event("llm.update", by, f"{name} ({detail})")
            return self.model(name)

    def delete(self, name: str) -> None:
        info = self.rt.get_llm()
        if info and info.get("model") == name and info["state"] == "running":
            raise LLMError("올라가 있는 모델은 지울 수 없습니다. 먼저 내려 주세요")
        self.db.execute("DELETE FROM llm_models WHERE name=?", (name,))
        self.db.log_event("llm.delete", None, name)

    # ── 올리기/내리기 ──
    def _sync_state(self) -> None:
        """서버가 재시작돼도 이미 떠 있는 llama-server 를 이어받는다."""
        try:
            info = self.rt.get_llm()
        except Exception:
            info = None
        if info and info["state"] == "running":
            self.state = "loading"
            self.loaded_at = self.last_used = info["started_at"] or time.time()

    def active(self) -> dict | None:
        """올라가 있는 llama-server 컨테이너 정보. Docker 에 묻는 일이라 2초 동안 기억해 둔다
        (/v1 요청마다 Docker 를 부르면 요청이 몰릴 때 서버 전체가 굼떠진다)."""
        now = time.monotonic()
        if self._active_at and now - self._active_at < 2.0:
            return self._active
        try:
            info = self.rt.get_llm()
        except Exception:
            # Docker 가 잠깐 응답하지 않아도 이미 떠 있는 llama-server 는 멀쩡하다. 한 번이라도 확인했던
            # 상태가 있으면 그것으로 계속 서비스하고, 다음 호출에서 다시 묻는다.
            if self._active_known:
                return self._active
            raise
        self._active = info if info and info["state"] == "running" else None
        self._active_at, self._active_known = now, True
        return self._active

    def _forget_active(self) -> None:
        self._active_at = 0.0

    def load(self, name: str, by: str) -> dict:
        with self._lock:
            model = self.model(name)
            if model is None:
                raise LLMError(f"등록되지 않은 모델: {name}")
            cur = self.active()
            if cur:
                if cur.get("model") == name:
                    return self.status()
                raise LLMError(f"이미 {cur.get('model')} 이(가) 올라가 있습니다. 먼저 내려 주세요")
            budget = self.sched.budget_gb
            if model["vram_gb"] > budget:
                raise LLMError(f"VRAM이 부족합니다 (필요 {model['vram_gb']:g}GB / 지금 여유 {budget:.1f}GB). "
                               "실행 중인 GPU 작업이 끝난 뒤 다시 시도하세요")
            self.rt.start_llm(model, loaded_by=by)
            self._forget_active()
            self.state, self.error = "loading", None
            self.loaded_at = self.last_used = time.time()
            self.db.log_event("llm.load", by, name)
            return self.status()

    def unload(self, by: str | None, reason: str = "") -> bool:
        with self._lock:
            cur = self.rt.get_llm()
            stopped = self.rt.stop_llm()
            self._active, self._active_at = None, 0.0      # 내린 뒤에는 예전 상태를 믿지 않는다
            self.state, self.error, self.loaded_at = "off", None, None
            if stopped:
                self.db.log_event("llm.unload", by, f"{cur.get('model') if cur else ''} {reason}".strip())
            return stopped

    def reserve_gb(self, service_used_gb: float) -> float:
        """로딩 중이라 아직 잡히지 않은 VRAM 을 큐 예산에서 미리 빼 둔다."""
        cur = self.active()
        if not cur:
            return 0.0
        model = self.model(cur.get("model") or "")
        return max(0.0, model["vram_gb"] - service_used_gb) if model else 0.0

    def observe_vram(self, used_gb: float) -> None:
        """llama-server 가 실제로 쓰는 GPU 메모리를 받아 둔다 (monitor 가 전체 수집 때마다 알려 준다).

        다 올라간 뒤의 최댓값은 모델 설정 옆에 남긴다. 예약값(vram_gb)은 사람이 어림해 적은 숫자라,
        실제로 얼마를 쓰는지 알아야 컨텍스트를 더 늘릴 여유가 있는지 가늠할 수 있다.
        """
        cur = self.active()
        if not cur or used_gb <= 0:
            self.vram_used_gb = None
            return
        self.vram_used_gb = round(used_gb, 2)
        model = self.model(cur.get("model") or "") if self.state == "ready" else None
        if model and used_gb > (model.get("measured_vram_gb") or 0) + 0.05:
            self.db.execute("UPDATE llm_models SET measured_vram_gb=? WHERE name=?",
                            (self.vram_used_gb, model["name"]))

    # ── 주기 점검 (monitor 스레드에서 호출) ──
    def tick(self, now: float | None = None) -> None:
        now = now or time.time()
        try:
            self.ip_users = self.rt.ip_map()
        except Exception:
            log.exception("ip_map failed")
        info = self.rt.get_llm()
        self._forget_active()
        if not info:
            self.state = "off"
            return
        if info["state"] != "running":
            self.state = "error"
            tail = self.rt.logs(info["id"], tail=15).strip().splitlines()
            self.error = tail[-1] if tail else f"llama-server 종료 (exit {info.get('exit_code')})"
            return
        if self.loaded_at is None:
            self.loaded_at = self.last_used = info["started_at"] or now
        if self.s.mock:
            self.state = "ready" if now - (info["started_at"] or now) > 3 else "loading"
        else:
            try:
                r = httpx.get(f"{self.base}/health", timeout=2)
                self.state = "ready" if r.status_code == 200 else "loading"
            except httpx.HTTPError:
                self.state = "loading"
        limit = self.s.llm_idle_unload_min * 60
        if self.state == "ready" and limit > 0 and self.last_used and now - self.last_used > limit:
            self.unload(None, f"{self.s.llm_idle_unload_min:g}분 동안 사용 없음")

    # ── 상태 ──
    def status(self) -> dict:
        cur = self.active()
        model = self.model(cur["model"]) if cur and cur.get("model") else None
        return {
            "state": self.state if cur else ("error" if self.state == "error" else "off"),
            "error": self.error,
            "model": model,
            "loaded_by": cur.get("loaded_by") if cur else None,
            "loaded_at": self.loaded_at if cur else None,
            "last_used": self.last_used if cur else None,
            "vram_used_gb": self.vram_used_gb if cur else None,
            "ctx_per_request": per_request_ctx(model) if model else None,
            "idle_unload_min": self.s.llm_idle_unload_min,
        }

    def user_for_ip(self, ip: str | None) -> str | None:
        hit = self.ip_users.get(ip or "")
        return hit["user"] if hit else None

    def usage(self, since: float) -> list[dict]:
        return self.db.all("""SELECT user, COUNT(*) AS requests, SUM(prompt_tokens) AS prompt_tokens,
                                     SUM(completion_tokens) AS completion_tokens, SUM(ms) AS ms, MAX(ts) AS last
                              FROM llm_usage WHERE ts >= ? GROUP BY user ORDER BY requests DESC""", (since,))

    def _record(self, user: str, source: str, model: str, usage: dict | None, t0: float, ok: bool) -> None:
        usage = usage or {}
        self.db.execute("""INSERT INTO llm_usage(ts, user, model, source, prompt_tokens, completion_tokens, ms, ok)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (time.time(), user, model, source, int(usage.get("prompt_tokens") or 0),
                         int(usage.get("completion_tokens") or 0), int((time.time() - t0) * 1000), int(ok)))

    # ── 프록시 ──
    async def proxy(self, path: str, method: str, body: bytes, user: str, source: str):
        cur = self.active()
        if path == "models":
            return JSONResponse({"object": "list", "data": [self._model_card(cur)] if cur else []})
        if not cur:
            return _err(503, "LLM이 올라가 있지 않습니다. 콘솔의 LLM 페이지에서 모델을 올려 주세요")
        if self.state != "ready":
            return _err(503, "모델을 올리는 중입니다. 잠시 뒤 다시 시도하세요")
        model_name = cur["model"]
        payload = None
        if body:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return _err(400, "요청 본문이 JSON이 아닙니다")
        if payload is not None and not isinstance(payload, dict):
            return _err(400, "요청 본문은 JSON 객체여야 합니다")
        stream = bool(payload and payload.get("stream"))
        if isinstance(payload, dict):
            payload["model"] = model_name
            if stream:
                payload.setdefault("stream_options", {})["include_usage"] = True
        self.last_used = time.time()
        t0 = time.time()

        if self.s.mock:
            return self._mock_response(payload or {}, stream, user, source, model_name, t0)

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5))
        req = self._client.build_request(method, f"{self.base}/v1/{path}",
                                         content=json.dumps(payload).encode() if payload is not None else body,
                                         headers={"content-type": "application/json"})
        try:
            resp = await self._client.send(req, stream=True)
        except httpx.HTTPError as e:
            self._record(user, source, model_name, None, t0, False)
            return _err(502, f"llama-server 에 연결하지 못했습니다: {e}")

        if not stream:
            data = await resp.aread()
            await resp.aclose()
            usage = None
            try:
                usage = json.loads(data).get("usage")
            except (json.JSONDecodeError, AttributeError):
                pass
            self._record(user, source, model_name, usage, t0, resp.status_code < 400)
            self.last_used = time.time()
            return Response(content=data, status_code=resp.status_code,
                            media_type=resp.headers.get("content-type", "application/json"))

        async def relay():
            usage, ok, buf = None, resp.status_code < 400, b""
            try:
                async for chunk in resp.aiter_raw():
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line.startswith(b"data: ") and b'"usage"' in line:
                            try:
                                u = json.loads(line[6:]).get("usage")
                                usage = u or usage
                            except json.JSONDecodeError:
                                pass
                    yield chunk
            except Exception:
                ok = False
                raise
            finally:
                await resp.aclose()
                self._record(user, source, model_name, usage, t0, ok)
                self.last_used = time.time()

        return StreamingResponse(relay(), status_code=resp.status_code, media_type="text/event-stream")

    def _model_card(self, cur: dict) -> dict:
        """/v1/models 의 항목. 요청 하나가 쓸 수 있는 컨텍스트 길이를 함께 알려 준다.

        이 값이 없으면 클라이언트(에이전트 도구 등)가 길이를 짐작하거나 사용자가 손으로 적어야 하고,
        틀리면 긴 대화에서 '컨텍스트를 넘었다'는 오류가 난다. 읽는 이름이 클라이언트마다 달라 둘 다 싣는다:
        context_length (OpenRouter 식), meta.n_ctx (llama.cpp 식).
        """
        card = {"id": cur["model"], "object": "model", "owned_by": "ironlab"}
        model = self.model(cur.get("model") or "")
        if model:
            n = per_request_ctx(model)
            card |= {"context_length": n, "meta": {"n_ctx": n, "n_parallel": model["parallel"]}}
        return card

    # ── mock 응답 (GPU 없이 화면·API 확인용) ──
    def _mock_response(self, payload: dict, stream: bool, user: str, source: str, model: str, t0: float):
        msgs = payload.get("messages") or [{"content": payload.get("prompt", "")}]
        last = msgs[-1].get("content") if msgs else ""
        if isinstance(last, list):
            last = " ".join(p.get("text", "") for p in last if isinstance(p, dict))
        reasoning = "질문을 정리하고 답을 구성합니다."
        text = (f"(mock 응답) {model} 입니다. '{str(last)[:60]}' 에 대해 답하면, 실제 서버에서는 "
                "llama.cpp 가 GPU 위에서 이 자리에 답을 스트리밍합니다.")
        usage = {"prompt_tokens": max(1, len(str(last)) // 2), "completion_tokens": len(text) // 2}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        cid = f"chatcmpl-mock{int(t0 * 1000)}"
        if not stream:
            self._record(user, source, model, usage, t0, True)
            return JSONResponse({"id": cid, "object": "chat.completion", "model": model, "created": int(t0),
                                 "choices": [{"index": 0, "finish_reason": "stop",
                                              "message": {"role": "assistant", "content": text,
                                                          "reasoning_content": reasoning}}],
                                 "usage": usage})

        async def gen():
            def chunk(delta, finish=None, use=None):
                d = {"id": cid, "object": "chat.completion.chunk", "model": model, "created": int(t0),
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if use is None else []}
                if use:
                    d["usage"] = use
                return f"data: {json.dumps(d, ensure_ascii=False)}\n\n".encode()
            for w in reasoning.split(" "):
                yield chunk({"reasoning_content": w + " "})
                await asyncio.sleep(0.02)
            for w in text.split(" "):
                yield chunk({"content": w + " "})
                await asyncio.sleep(0.03)
            yield chunk({}, "stop")
            yield chunk({}, use=usage)
            yield b"data: [DONE]\n\n"
            self._record(user, source, model, usage, t0, True)
            self.last_used = time.time()

        return StreamingResponse(gen(), media_type="text/event-stream")
