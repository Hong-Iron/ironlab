"""GPU 작업 큐.

GeForce 계열(3090·4090 등)은 MIG(하드웨어 분할)가 없어서 VRAM을 강제로 나눌 수 없다. 그래서 작업마다
필요한 VRAM을 '예약'으로 선언받고, 예약 합계가 GPU 용량을 넘지 않을 때만 실행한다.

  여유 = 전체 − 안전여유 − (실행 중 작업 예약/실사용 중 큰 값의 합) − (큐 밖 사용량: 워크스페이스 등)

- 순서: 먼저 낸 작업 우선(FIFO). 앞 작업이 VRAM 부족으로 못 돌면 뒤의 작은 작업을 끼워 넣는다(backfill).
- 기아 방지: 맨 앞 작업이 starvation_minutes 이상 기다리면 끼워넣기를 멈추고 그 작업을 기다린다.
- 사용자당 동시 실행 수 제한, 독점(exclusive) 작업은 GPU를 혼자 쓴다.
- 선언보다 VRAM을 많이 쓰는 작업은 경고하거나(warn) 종료한다(kill).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .config import Settings
from .db import DB


MAX_START_ATTEMPTS = 3
KEEP_LOG_LINES = 20000      # 끝난 작업의 로그로 남길 마지막 줄 수


class JobError(ValueError):
    pass


@dataclass
class Plan:
    start: list[int] = field(default_factory=list)
    reasons: dict[int, str] = field(default_factory=dict)
    budget_gb: float = 0.0


def plan_admissions(queued: list[dict], running: list[dict], *, total_gb: float,
                    reserve_gb: float, gpu_used_gb: float, actual_gb: dict[int, float],
                    max_per_user: int, starvation_s: float, now: float,
                    extra_reserved_gb: float = 0.0) -> Plan:
    """어떤 대기 작업을 지금 시작할지 정한다 (부수효과 없는 순수 함수)."""
    plan = Plan()
    jobs_actual = sum(actual_gb.get(j["id"], 0.0) for j in running)
    untracked = max(0.0, gpu_used_gb - jobs_actual)
    reserved = sum(max(j["vram_gb"], actual_gb.get(j["id"], 0.0)) for j in running)
    # extra_reserved_gb: 로딩 중이라 아직 VRAM 을 다 잡지 않은 서비스(LLM)의 몫
    budget = total_gb - reserve_gb - reserved - untracked - extra_reserved_gb
    capacity = total_gb - reserve_gb

    per_user: dict[str, int] = {}
    for j in running:
        per_user[j["user"]] = per_user.get(j["user"], 0) + 1
    n_running = len(running)
    exclusive_running = any(j["exclusive"] for j in running)

    order = sorted(queued, key=lambda j: (j["created_at"], j["id"]))
    strict = bool(order) and (now - order[0]["created_at"]) >= starvation_s

    for idx, j in enumerate(order):
        if strict and idx > 0:
            plan.reasons[j["id"]] = "앞선 작업 먼저 실행 대기 (기아 방지)"
            continue
        if j["vram_gb"] > capacity:
            reason = f"요청 VRAM({j['vram_gb']:g}GB)이 GPU 용량({capacity:g}GB)보다 큼"
        elif exclusive_running:
            reason = "독점 작업 실행 중"
        elif per_user.get(j["user"], 0) >= max_per_user:
            reason = f"사용자 동시 실행 한도({max_per_user}개)"
        elif j["exclusive"] and n_running > 0:
            reason = "독점 실행: 다른 작업이 끝나길 대기"
        elif j["vram_gb"] > budget:
            reason = f"VRAM 대기 (필요 {j['vram_gb']:g}GB / 여유 {max(budget, 0):.1f}GB)"
        else:
            plan.start.append(j["id"])
            budget -= j["vram_gb"]
            per_user[j["user"]] = per_user.get(j["user"], 0) + 1
            n_running += 1
            exclusive_running = exclusive_running or bool(j["exclusive"])
            continue
        plan.reasons[j["id"]] = reason
    plan.budget_gb = round(max(budget, 0.0), 2)
    return plan


class Scheduler:
    def __init__(self, db: DB, settings: Settings, runtime, gpu_total_gb: float):
        self.db, self.s, self.rt = db, settings, runtime
        self.total_gb = settings.gpu_total_vram_gb or gpu_total_gb or 24.0
        self.reasons: dict[int, str] = {}
        self.budget_gb = 0.0
        self._overuse_warned: set[int] = set()
        self._start_attempts: dict[int, int] = {}
        self.lock = threading.RLock()  # tick과 cancel이 같은 작업을 동시에 정리하지 않도록

    # ── 제출/취소 ──
    def submit(self, user: dict, *, name: str, command: str, vram_gb: float,
               cpus: float | None = None, mem_gb: int | None = None,
               exclusive: bool = False, workdir: str = "") -> dict:
        command = command.strip()
        if not command:
            raise JobError("명령이 비어 있습니다")
        capacity = self.total_gb - self.s.vram_reserve_gb
        if exclusive:
            vram_gb = capacity
        if not (0 < vram_gb <= capacity):
            raise JobError(f"VRAM은 0보다 크고 {capacity:g}GB 이하여야 합니다")
        if user["max_vram_gb"] and vram_gb > user["max_vram_gb"]:
            raise JobError(f"사용자 VRAM 한도({user['max_vram_gb']:g}GB)를 넘습니다")
        cpus = cpus or user["cpus"]
        mem_gb = mem_gb or user["mem_gb"]
        if cpus > user["cpus"] or mem_gb > user["mem_gb"]:
            raise JobError(f"CPU/RAM 한도는 {user['cpus']:g}코어 / {user['mem_gb']}GB 입니다")
        if ".." in workdir.split("/"):
            raise JobError("workdir에 '..'를 쓸 수 없습니다")
        pending = self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE user=? AND status='queued'",
                              (user["name"],))["n"]
        if pending >= 20:
            raise JobError("대기 중인 작업이 너무 많습니다 (최대 20개)")
        cur = self.db.execute(
            """INSERT INTO jobs(user, name, command, workdir, vram_gb, cpus, mem_gb, exclusive, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (user["name"], (name or command.split()[0])[:80], command, workdir.strip("/"),
             float(vram_gb), float(cpus), int(mem_gb), int(exclusive), time.time()))
        self.db.log_event("job.submit", user["name"], f"#{cur.lastrowid} vram={vram_gb:g}GB")
        return self.get(cur.lastrowid)

    def cancel(self, job_id: int, by: str) -> dict:
        with self.lock:
            return self._cancel(job_id, by)

    def _cancel(self, job_id: int, by: str) -> dict:
        job = self.get(job_id)
        if job is None:
            raise JobError("없는 작업")
        if job["status"] == "queued":
            self.db.execute("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?",
                            (time.time(), job_id))
        elif job["status"] == "running":
            self.rt.kill(job["container_id"])
            self._finalize(job, status="cancelled")
        else:
            raise JobError(f"이미 끝난 작업입니다 ({job['status']})")
        self.db.log_event("job.cancel", by, f"#{job_id}")
        return self.get(job_id)

    def get(self, job_id: int) -> dict | None:
        return self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def logs(self, job: dict, tail: int | None = 200) -> str:
        if job["status"] == "running" and job["container_id"]:
            return self.rt.logs(job["container_id"], tail)
        path = self.s.logs_dir / f"job-{job['id']}.log"
        if not path.exists():
            return ""
        lines = path.read_text(errors="replace").splitlines()
        return "\n".join(lines[-tail:] if tail else lines) + "\n"

    # ── 주기 실행 ──
    def tick(self, gpu_used_gb: float, actual_gb: dict[int, float], extra_reserved_gb: float = 0.0,
             hold: str | None = None) -> None:
        """hold: 새 작업을 시작하면 안 되는 이유 (예: GPU 상태를 읽지 못함). 끝난 작업 정리는 그대로 한다."""
        with self.lock:
            self._tick(gpu_used_gb, actual_gb, extra_reserved_gb, hold)

    def reconcile(self, containers: list[dict]) -> int:
        """DB 가 모르는 작업 컨테이너(고아)를 정리한다.

        Docker 가 컨테이너를 띄웠는데 응답이 유실됐거나, 띄운 직후 서버가 죽으면 컨테이너는 GPU 를 쓰며
        돌고 있는데 ironlab 은 그 작업을 실행 중으로 알지 못한다. 그대로 두면 아무도 끝내 주지 않는다.
        """
        removed = 0
        with self.lock:
            for c in containers:
                if c.get("kind") != "job":
                    continue
                job = self.get(int(c["job_id"])) if str(c.get("job_id") or "").isdigit() else None
                if job and job["status"] == "running" and job["container_id"] == c["id"]:
                    continue
                try:
                    self.rt.kill(c["id"])
                    self.rt.remove(c["id"])
                except Exception:
                    continue        # 다음 주기에 다시 시도한다
                removed += 1
                self.db.log_event("job.orphan_removed", c.get("user"),
                                  f"#{c.get('job_id')} 기록에 없는 작업 컨테이너를 정리했습니다")
        return removed

    def _tick(self, gpu_used_gb: float, actual_gb: dict[int, float], extra_reserved_gb: float = 0.0,
              hold: str | None = None) -> None:
        self._reap(actual_gb)
        running = self.db.all("SELECT * FROM jobs WHERE status='running'")
        queued = self.db.all("SELECT * FROM jobs WHERE status='queued'")
        if hold:
            # 지금 GPU 가 얼마나 비었는지 모르는 채로 작업을 시작하면 메모리 부족으로 남의 작업까지 죽일 수 있다
            self.reasons = {j["id"]: hold for j in queued}
            return
        plan = plan_admissions(
            queued, running, total_gb=self.total_gb, reserve_gb=self.s.vram_reserve_gb,
            gpu_used_gb=gpu_used_gb, actual_gb=actual_gb,
            max_per_user=self.s.max_running_jobs_per_user,
            starvation_s=self.s.starvation_minutes * 60, now=time.time(),
            extra_reserved_gb=extra_reserved_gb)
        self.reasons, self.budget_gb = plan.reasons, plan.budget_gb
        for job_id in plan.start:
            self._start(self.get(job_id))

    def _start(self, job: dict) -> None:
        user = self.db.get_user(job["user"])
        try:
            if user is None or user["disabled"]:
                raise JobError("비활성화된 사용자")
            cid = self.rt.run_job(job, user)
        except Exception as e:
            # Docker 가 잠깐 응답하지 않은 것일 수 있다. 작업 잘못이 아니므로 몇 번은 다시 시도한다.
            # (응답만 유실되고 컨테이너는 떴을 수 있으니, 같은 이름의 컨테이너를 먼저 치운다)
            n = self._start_attempts.get(job["id"], 0) + 1
            try:
                from .runtime import job_name
                self.rt.remove(job_name(job["id"]))
            except Exception:
                pass
            if not isinstance(e, JobError) and n < MAX_START_ATTEMPTS:
                self._start_attempts[job["id"]] = n
                self.reasons[job["id"]] = f"시작하지 못해 다시 시도합니다 ({n}/{MAX_START_ATTEMPTS}): {e}"
                self.db.log_event("job.start_retry", job["user"], f"#{job['id']} {e}")
                return
            self._start_attempts.pop(job["id"], None)
            self.db.execute("UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
                            (f"시작 실패: {e}", time.time(), job["id"]))
            self.db.log_event("job.start_failed", job["user"], f"#{job['id']} {e}")
            return
        self._start_attempts.pop(job["id"], None)
        self.db.execute("UPDATE jobs SET status='running', container_id=?, started_at=? WHERE id=?",
                        (cid, time.time(), job["id"]))
        self.db.log_event("job.start", job["user"], f"#{job['id']}")

    def _reap(self, actual_gb: dict[int, float]) -> None:
        for job in self.db.all("SELECT * FROM jobs WHERE status='running'"):
            used = actual_gb.get(job["id"], 0.0)
            if used > job["peak_vram_gb"]:
                self.db.execute("UPDATE jobs SET peak_vram_gb=? WHERE id=?", (round(used, 2), job["id"]))
            if used > job["vram_gb"] * 1.1 + 0.5 and job["id"] not in self._overuse_warned:
                self._overuse_warned.add(job["id"])
                msg = f"#{job['id']} 선언 {job['vram_gb']:g}GB / 실사용 {used:.1f}GB"
                self.db.log_event("job.vram_overuse", job["user"], msg)
                if self.s.vram_overuse_action == "kill":
                    self.rt.kill(job["container_id"])
                    self._finalize(job, status="failed", error=f"VRAM 초과 사용으로 종료 ({msg})")
                    continue

            info = self.rt.inspect(job["container_id"]) if job["container_id"] else None
            if info is None:
                self._finalize(job, status="failed", error="컨테이너가 사라졌습니다")
            elif info["state"] != "running":
                code = info["exit_code"]
                self._finalize(job, status="succeeded" if code == 0 else "failed", exit_code=code)

    def _finalize(self, job: dict, *, status: str, exit_code: int | None = None,
                  error: str | None = None) -> None:
        cid = job["container_id"]
        if cid:
            try:
                # 마지막 부분만 남긴다. 진행률을 계속 찍는 작업의 로그를 통째로 메모리에 올려 저장하지 않는다
                text = self.rt.logs(cid, KEEP_LOG_LINES)
                (self.s.logs_dir / f"job-{job['id']}.log").write_text(text)
            except Exception:
                pass
            if exit_code is None:
                info = self.rt.inspect(cid)
                exit_code = info["exit_code"] if info else None
            try:
                self.rt.remove(cid)
            except Exception:
                pass
        self.db.execute(
            "UPDATE jobs SET status=?, exit_code=?, error=COALESCE(?, error), finished_at=? WHERE id=?",
            (status, exit_code, error, time.time(), job["id"]))
        self.db.log_event(f"job.{status}", job["user"], f"#{job['id']} exit={exit_code}")
