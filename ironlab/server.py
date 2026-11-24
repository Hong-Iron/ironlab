"""서버 실행 및 최초 설정용 명령.

  ironlab-server serve                         API·대시보드 실행
  ironlab-server create-admin NAME [--tailscale-login LOGIN]
  ironlab-server demo                          mock 모드로 데모 사용자·작업을 만들어 실행
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_settings
from .db import DB
from . import users as um


def _serve(args) -> None:
    import asyncio
    import uvicorn
    from .api import create_app, create_internal_app
    settings = load_settings()
    if args.mock:
        settings.mock = True
    app = create_app(settings)
    main = uvicorn.Server(uvicorn.Config(app, host=args.host or settings.host,
                                         port=args.port or settings.port, log_level="info"))

    async def run():
        internal = None if settings.mock else InternalProxy(
            create_internal_app(app.state.ctx), settings.network_gateway, settings.internal_port)
        task = asyncio.create_task(internal.run()) if internal else None
        await main.serve()          # Ctrl+C / systemctl stop 으로 끝나면
        if internal:
            await internal.stop(task)   # 내부 프록시도 함께 닫는다
    asyncio.run(run())


class InternalProxy:
    """워크스페이스 컨테이너용 LLM 프록시. ironlab 네트워크의 게이트웨이 IP 에만 연다.

    이 주소는 Docker 가 네트워크를 만들어야 생긴다. 아직 없으면(부팅 직후, Docker 가 늦게 뜸) 대시보드까지
    함께 죽지 않도록, 열릴 때까지 주기적으로 다시 시도한다.
    """

    def __init__(self, app, host: str, port: int, retry_s: float = 10.0):
        self.app, self.host, self.port, self.retry_s = app, host, port, retry_s
        self.server = None      # 주소를 여는 데 성공한 뒤에만 생긴다

    async def run(self) -> None:
        import asyncio
        import socket
        import uvicorn
        log = logging.getLogger("ironlab")
        warned = False
        while True:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((self.host, self.port))
                break
            except OSError as e:
                sock.close()
                if not warned:
                    log.warning("내부 LLM 프록시를 %s:%s 에 열지 못했습니다 (%s). %g초마다 다시 시도합니다. "
                                "그동안 컨테이너 안에서는 로컬 LLM 을 쓸 수 없습니다",
                                self.host, self.port, e, self.retry_s)
                    warned = True
                await asyncio.sleep(self.retry_s)
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="warning"))
        self.server.install_signal_handlers = lambda: None      # 종료 신호는 메인 서버가 받는다
        log.info("내부 LLM 프록시: http://%s:%s/v1", self.host, self.port)
        try:
            await self.server.serve(sockets=[sock])
        finally:
            sock.close()

    async def stop(self, task) -> None:
        import asyncio
        if self.server is not None:
            self.server.should_exit = True      # 열려 있으면 정상 종료를 기다린다
        else:
            task.cancel()                       # 아직 재시도 중이면 그만둔다
        await asyncio.gather(task, return_exceptions=True)


def _create_admin(args) -> None:
    s = load_settings()
    s.ensure_dirs()
    db = DB(s.db_path)
    try:
        user, token = um.create_user(db, s, args.name, role="admin", tailscale_login=args.tailscale_login)
    except um.UserError as e:
        sys.exit(f"오류: {e}")
    print(f"관리자 '{user['name']}' 생성 완료")
    print(f"토큰 (다시 볼 수 없으니 저장하세요): {token}")


def _demo(args) -> None:
    """mock 모드 데모: 임시 디렉터리에 사용자 3명과 작업 몇 개를 만들고 서버를 띄운다."""
    import tempfile
    from pathlib import Path
    import uvicorn
    from .api import create_app

    base = Path(args.dir or tempfile.mkdtemp(prefix="ironlab-demo-"))
    s = load_settings(mock=True, data_dir=base / "data", users_root=base / "users",
                      shared_root=base / "shared", scratch_root=base / "scratch",
                      monitor_interval_s=2.0, public_host="gpubox.tailnet.ts.net")
    app = create_app(s)
    ctx = app.state.ctx
    tokens = {}
    for name, role in (("cheolmin", "admin"), ("seoyeon", "user"), ("jihoon", "user")):
        if ctx.db.get_user(name):
            tokens[name] = um.rotate_token(ctx.db, name)
        else:
            _, tokens[name] = um.create_user(ctx.db, s, name, role=role)
    (s.users_root / "cheolmin" / "trellis-runs").mkdir(exist_ok=True)
    (s.users_root / "cheolmin" / "README.txt").write_text("데모 파일입니다.\n")
    (s.shared_root / "models" / "hub").mkdir(exist_ok=True)

    for name in ("cheolmin", "seoyeon"):
        ctx.rt.start_workspace(ctx.db.get_user(name))
    if not ctx.llm.model("qwen3.8-27b"):
        ctx.llm.register(name="qwen3.8-27b", file="qwen3.8-27b/Qwen3.8-27B-UD-Q4_K_XL.gguf",
                         vram_gb=20, ctx=32768, parallel=2)
    for user, cmd, vram in (("seoyeon", "python gen.py --res 1536 && sleep 120", 20),
                            ("jihoon", "python trellis_batch.py && sleep 90", 10),
                            ("cheolmin", "python pixal3d.py --low_vram && sleep 60", 8),
                            ("jihoon", "python train_lora.py && sleep 200", 14)):
        ctx.sched.submit(ctx.db.get_user(user), name=cmd.split()[1], command=cmd, vram_gb=vram)

    print(f"\n데모 데이터: {base}")
    print("로그인 토큰:")
    for n, t in tokens.items():
        print(f"  {n:9s} {t}")
    print(f"\n브라우저에서 http://{args.host}:{args.port} 를 여세요\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="ironlab-server")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("serve", help="API·대시보드 실행")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.add_argument("--mock", action="store_true", help="Docker/GPU 없이 가짜 백엔드로 실행")
    sp.set_defaults(fn=_serve)

    ca = sub.add_parser("create-admin", help="최초 관리자 계정 생성")
    ca.add_argument("name")
    ca.add_argument("--tailscale-login", help="예: you@github")
    ca.set_defaults(fn=_create_admin)

    dm = sub.add_parser("demo", help="mock 데모 실행")
    dm.add_argument("--host", default="127.0.0.1")
    dm.add_argument("--port", type=int, default=8700)
    dm.add_argument("--dir", help="데모 데이터 디렉터리 (기본: 임시 폴더)")
    dm.set_defaults(fn=_demo)

    args = p.parse_args(argv)
    if not getattr(args, "fn", None):
        args = p.parse_args(["serve", *(argv or sys.argv[1:])])
    args.fn(args)


if __name__ == "__main__":
    main()
