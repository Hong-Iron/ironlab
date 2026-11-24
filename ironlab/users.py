"""사용자 계정: 생성, 토큰, 홈 디렉터리, SSH 키."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import time

from .config import Settings
from .db import DB

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
KEY_RE = re.compile(r"^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com) [A-Za-z0-9+/=]+( .*)?$")


class UserError(ValueError):
    pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return "il_" + secrets.token_urlsafe(32)


def _chown(path, uid: int, mock: bool) -> None:
    if mock or os.geteuid() != 0:
        return
    os.chown(path, uid, uid)


def provision_dirs(settings: Settings, name: str, uid: int) -> None:
    home = settings.users_root / name
    scratch = settings.scratch_root / name
    for d in (home, scratch):
        d.mkdir(parents=True, exist_ok=True)
        _chown(d, uid, settings.mock)
        if not settings.mock:
            os.chmod(d, 0o700)
    write_authorized_keys(settings, name, "")


def write_authorized_keys(settings: Settings, name: str, keys: str) -> None:
    d = settings.keys_dir / name
    d.mkdir(parents=True, exist_ok=True)
    # 새 파일을 다 쓴 뒤 이름을 바꿔 끼운다. 그냥 덮어쓰면 쓰는 도중 서버가 죽거나 전원이 나갈 때
    # 빈 파일이 남아, 키를 등록해 둔 사용자가 SSH 로 못 들어오게 된다.
    tmp = d / ".authorized_keys.new"
    tmp.write_text(keys.strip() + ("\n" if keys.strip() else ""))
    os.chmod(tmp, 0o644)
    os.replace(tmp, d / "authorized_keys")
    # sshd StrictModes: root 소유, 다른 사용자 쓰기 불가
    os.chmod(d, 0o755)


def sync_authorized_keys(db: DB, settings: Settings) -> int:
    """DB 에 등록된 키와 키 파일을 맞춘다 (서버를 띄울 때). 파일이 없어졌거나 비었거나 달라졌으면 다시 쓴다."""
    fixed = 0
    for u in db.list_users():
        want = (u["ssh_pubkeys"] or "").strip()
        f = settings.keys_dir / u["name"] / "authorized_keys"
        try:
            have = f.read_text().strip()
        except OSError:
            have = None
        if have != want:
            write_authorized_keys(settings, u["name"], want)
            fixed += 1
    return fixed


def validate_pubkeys(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        raise UserError("SSH 공개키가 비어 있습니다")
    for ln in lines:
        if not KEY_RE.match(ln):
            raise UserError(f"올바른 SSH 공개키 형식이 아닙니다: {ln[:40]}…")
    return "\n".join(lines)


def create_user(db: DB, settings: Settings, name: str, *, role: str = "user",
                tailscale_login: str | None = None, cpus: float | None = None,
                mem_gb: int | None = None, disk_gb: int | None = None,
                max_vram_gb: float | None = None) -> tuple[dict, str]:
    """사용자를 만들고 (사용자, 평문 토큰)을 반환한다. 토큰은 이때만 볼 수 있다."""
    if not NAME_RE.match(name):
        raise UserError("이름은 영문 소문자로 시작하는 2~32자 (a-z, 0-9, _, -)여야 합니다")
    if role not in ("user", "admin"):
        raise UserError("role은 user 또는 admin")
    if db.get_user(name):
        raise UserError(f"이미 있는 사용자: {name}")
    tailscale_login = (tailscale_login or "").strip() or None    # 빈칸은 '연결 없음'
    if tailscale_login and db.one("SELECT 1 FROM users WHERE tailscale_login=?", (tailscale_login,)):
        raise UserError(f"이미 연결된 Tailscale 계정: {tailscale_login}")

    uid = db.next_uid(settings.uid_base)
    token = new_token()
    db.execute(
        """INSERT INTO users(name, role, uid, token_hash, tailscale_login, cpus, mem_gb,
                             disk_gb, max_vram_gb, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (name, role, uid, hash_token(token), tailscale_login,
         cpus or settings.default_cpus, mem_gb or settings.default_mem_gb,
         disk_gb or settings.default_disk_gb, max_vram_gb, time.time()))
    provision_dirs(settings, name, uid)
    db.log_event("user.create", name, f"role={role}")
    return db.get_user(name), token


def rotate_token(db: DB, name: str) -> str:
    token = new_token()
    db.execute("UPDATE users SET token_hash=? WHERE name=?", (hash_token(token), name))
    db.log_event("user.token_rotate", name)
    return token


UPDATABLE = {"role", "tailscale_login", "cpus", "mem_gb", "disk_gb", "max_vram_gb", "disabled"}


def update_user(db: DB, name: str, **changes) -> dict:
    current = db.get_user(name)
    if not current:
        raise UserError(f"없는 사용자: {name}")
    changes = {k: v for k, v in changes.items() if k in UPDATABLE and v is not None}
    if "role" in changes and changes["role"] not in ("user", "admin"):
        raise UserError("role은 user 또는 admin")
    if "tailscale_login" in changes:
        login = changes["tailscale_login"].strip() or None       # 빈칸이면 연결을 끊는다 (NULL)
        if login and db.one("SELECT 1 FROM users WHERE tailscale_login=? AND name<>?", (login, name)):
            raise UserError(f"이미 연결된 Tailscale 계정: {login}")
        changes["tailscale_login"] = login
    if changes.get("max_vram_gb") == 0:
        changes["max_vram_gb"] = None                             # 0 = 한도 없음
    # 관리자가 한 명도 남지 않게 되는 변경은 막는다 (아무도 사용자 관리를 할 수 없게 된다)
    loses_admin = current["role"] == "admin" and not current["disabled"] and (
        changes.get("role") == "user" or changes.get("disabled"))
    if loses_admin and not db.one("SELECT 1 FROM users WHERE role='admin' AND disabled=0 AND name<>?", (name,)):
        raise UserError("마지막 관리자입니다. 다른 관리자를 먼저 지정하세요")
    if changes:
        cols = ", ".join(f"{k}=?" for k in changes)     # 열 이름은 UPDATABLE 에 있는 것만 온다
        db.execute(f"UPDATE users SET {cols} WHERE name=?", (*changes.values(), name))  # noqa: S608
        db.log_event("user.update", name, ", ".join(f"{k}={v}" for k, v in changes.items()))
    return db.get_user(name)


def ssh_port(settings: Settings, user: dict) -> int:
    return settings.ssh_port_base + (user["uid"] - settings.uid_base)


def public_view(user: dict, settings: Settings) -> dict:
    """토큰 해시 등 민감 정보를 뺀 사용자 정보."""
    return {
        "name": user["name"], "role": user["role"], "uid": user["uid"],
        "tailscale_login": user["tailscale_login"], "cpus": user["cpus"],
        "mem_gb": user["mem_gb"], "disk_gb": user["disk_gb"],
        "max_vram_gb": user["max_vram_gb"], "disabled": bool(user["disabled"]),
        "has_ssh_key": bool(user["ssh_pubkeys"]), "ssh_port": ssh_port(settings, user),
        "created_at": user["created_at"], "last_seen": user["last_seen"],
    }
