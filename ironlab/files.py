"""사용자 파일 관리: 홈/스크래치/공용 영역 탐색, 업로드·다운로드, 쿼터.

모든 경로는 영역 루트 안으로만 해석된다 (`..`, 절대경로, 루트 밖을 가리키는 심볼릭 링크 차단).
"""

from __future__ import annotations

import os
import secrets
import shutil
import stat
import time
from pathlib import Path
from typing import BinaryIO

from .config import Settings

AREAS = ("home", "scratch", "shared")
CHUNK = 1024 * 1024
_usage_cache: dict[str, tuple[float, int]] = {}


class FileError(ValueError):
    pass


class QuotaExceeded(FileError):
    pass


def area_root(settings: Settings, user: dict, area: str) -> tuple[Path, bool]:
    """(루트 경로, 쓰기 가능 여부)."""
    if area == "home":
        return settings.users_root / user["name"], True
    if area == "scratch":
        return settings.scratch_root / user["name"], True
    if area == "shared":
        return settings.shared_root, user["role"] == "admin"
    raise FileError(f"알 수 없는 영역: {area}")


def resolve(root: Path, rel: str) -> Path:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    root_r = root.resolve()
    target = (root_r / rel).resolve()
    if not target.is_relative_to(root_r):
        raise FileError("영역 밖의 경로입니다")
    return target


def check_name(name: str) -> str:
    name = name.strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\0" in name or len(name) > 255:
        raise FileError(f"쓸 수 없는 이름: {name!r}")
    return name


# ── 심볼릭 링크를 따라가지 않는 접근 ──
#
# 서버는 root 로 돌고, 홈·스크래치 안의 내용은 사용자가 컨테이너에서 마음대로 바꿀 수 있다.
# 경로 문자열을 검사한 뒤 그 문자열로 다시 여는 방식은, 사용자가 그 사이에 폴더를 심볼릭 링크로
# 바꾸거나 임시 파일 이름으로 링크를 미리 만들어 두면 root 권한으로 호스트의 다른 파일을 읽고 쓰게 된다.
# 그래서 사용자가 쓸 수 있는 영역에서는 폴더를 한 단계씩 O_NOFOLLOW 로 열어 fd 를 얻고,
# 그 fd 를 기준으로만(openat 계열) 파일을 만들고 지운다.
# 공용 영역은 관리자만 쓸 수 있고 HF 캐시가 링크로 이뤄져 있으므로, 영역 안을 가리키는 링크는 따라간다.

def _parts(rel: str) -> list[str]:
    parts = [p for p in (rel or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        raise FileError("영역 밖의 경로입니다")
    return parts


def _open_dir(root: Path, rel: str, strict: bool) -> int:
    """root 아래 rel 폴더를 연 fd. strict 면 경로 어디에도 심볼릭 링크를 허용하지 않는다. 호출한 쪽이 닫는다."""
    if not strict:
        target = resolve(root, rel)
        if not target.is_dir():
            raise FileError("폴더가 아닙니다")
        return os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in _parts(rel):
            try:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                raise FileError("없는 폴더입니다") from None
            except OSError:
                raise FileError("폴더가 아니거나 심볼릭 링크입니다 (링크는 여기서 열 수 없습니다)") from None
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _split(rel: str) -> tuple[str, str]:
    """'a/b/c' → ('a/b', 'c'). 이름은 검사해서 돌려준다."""
    rel = (rel or "").replace("\\", "/").strip("/")
    parent, _, name = rel.rpartition("/")
    return parent, check_name(name)


def list_dir(root: Path, rel: str, strict: bool = True) -> dict:
    fd = _open_dir(root, rel, strict)
    entries = []
    try:
        with os.scandir(fd) as it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                is_dir = stat.S_ISDIR(st.st_mode)
                entries.append({
                    "name": e.name, "is_dir": is_dir, "is_link": stat.S_ISLNK(st.st_mode),
                    "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime,
                })
    finally:
        os.close(fd)
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    if strict:
        path = "/".join(_parts(rel))
    else:
        d = resolve(root, rel)
        path = "" if d == root.resolve() else str(d.relative_to(root.resolve()))
    return {"path": path, "entries": entries}


def disk_usage_bytes(path: Path) -> int:
    """심볼릭 링크를 따라가지 않고 파일 크기를 합산."""
    total = 0
    stack = [str(path)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def cached_usage(path: Path, ttl: float = 60.0) -> int:
    key = str(path)
    hit = _usage_cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    used = disk_usage_bytes(path)
    _usage_cache[key] = (time.time(), used)
    return used


def _bump_usage(path: Path, delta: int) -> None:
    key = str(path)
    if key in _usage_cache:
        ts, used = _usage_cache[key]
        _usage_cache[key] = (ts, max(0, used + delta))


def quota_info(settings: Settings, user: dict) -> dict:
    home = settings.users_root / user["name"]
    scratch = settings.scratch_root / user["name"]
    return {
        "home_used": cached_usage(home), "scratch_used": cached_usage(scratch),
        "quota": user["disk_gb"] * 2**30,
    }


def _owned(settings: Settings, area: str) -> bool:
    """만든 파일을 사용자 소유로 넘길지. mock 이거나 root 가 아니면 소유자를 바꿀 수 없다."""
    return area != "shared" and not settings.mock and os.geteuid() == 0


def save_upload(settings: Settings, user: dict, area: str, rel_dir: str,
                filename: str, src: BinaryIO) -> dict:
    root, writable = area_root(settings, user, area)
    if not writable:
        raise FileError("이 영역은 읽기 전용입니다")
    name = check_name(filename)
    dfd = _open_dir(root, rel_dir, strict=area != "shared")
    tmp = f".{name[:200]}.uploading-{secrets.token_hex(4)}"
    made = False
    try:
        try:
            cur = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            cur = None
        if cur is not None and stat.S_ISDIR(cur.st_mode):
            raise FileError("같은 이름의 폴더가 있습니다")
        old = cur.st_size if cur is not None and stat.S_ISREG(cur.st_mode) else 0

        # 홈만 쿼터를 적용 (스크래치는 주기적으로 비우는 공간)
        limit = None
        if area == "home":
            limit = user["disk_gb"] * 2**30 - cached_usage(root) + old
            if limit <= 0:
                raise QuotaExceeded("디스크 할당량을 모두 썼습니다")

        # O_EXCL: 같은 이름이 이미 있으면(링크 포함) 실패한다. 미리 만들어 둔 링크를 따라가 쓰는 일이 없다.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dfd)
        made = True
        written = 0
        with os.fdopen(fd, "wb") as out:
            if _owned(settings, area):
                os.fchown(out.fileno(), user["uid"], user["uid"])      # 경로가 아니라 연 파일 자체의 소유자를 바꾼다
            while chunk := src.read(CHUNK):
                written += len(chunk)
                if limit is not None and written > limit:
                    raise QuotaExceeded("업로드하면 디스크 할당량을 넘습니다")
                out.write(chunk)
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)       # 이름이 링크였다면 링크 자체가 교체된다
        made = False
    finally:
        if made:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
        os.close(dfd)
    _bump_usage(root, written - old)
    return {"name": name, "size": written}


def make_dir(settings: Settings, user: dict, area: str, rel_dir: str, name: str) -> None:
    root, writable = area_root(settings, user, area)
    if not writable:
        raise FileError("이 영역은 읽기 전용입니다")
    name = check_name(name)
    dfd = _open_dir(root, rel_dir, strict=area != "shared")
    try:
        try:
            os.mkdir(name, 0o755, dir_fd=dfd)
        except FileExistsError:
            raise FileError("이미 있는 이름입니다") from None
        if _owned(settings, area):
            os.chown(name, user["uid"], user["uid"], dir_fd=dfd, follow_symlinks=False)
    finally:
        os.close(dfd)


def delete_path(settings: Settings, user: dict, area: str, rel: str, recursive: bool) -> None:
    root, writable = area_root(settings, user, area)
    if not writable:
        raise FileError("이 영역은 읽기 전용입니다")
    if not _parts(rel):
        raise FileError("영역 루트는 지울 수 없습니다")
    parent, name = _split(rel)
    dfd = _open_dir(root, parent, strict=area != "shared")
    try:
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            raise FileError("없는 경로입니다") from None
        if not stat.S_ISDIR(st.st_mode):
            os.unlink(name, dir_fd=dfd)                 # 링크면 링크 자체만 지운다
            _bump_usage(root, -st.st_size)
        elif recursive:
            shutil.rmtree(name, dir_fd=dfd)             # fd 기준 삭제: 도중에 링크로 바꿔치기해도 밖으로 나가지 않는다
            _usage_cache.pop(str(root), None)
        else:
            try:
                os.rmdir(name, dir_fd=dfd)
            except OSError:
                raise FileError("폴더가 비어 있지 않습니다 (recursive=true 필요)") from None
    finally:
        os.close(dfd)


def open_for_download(settings: Settings, user: dict, area: str, rel: str) -> tuple[BinaryIO, str, int]:
    """(열린 파일, 이름, 크기). 검사한 바로 그 파일을 열어서 돌려주므로, 검사 뒤에 바꿔치기할 틈이 없다."""
    root, _ = area_root(settings, user, area)
    if not _parts(rel):
        raise FileError("파일이 아닙니다")
    parent, name = _split(rel)
    strict = area != "shared"
    dfd = _open_dir(root, parent, strict)
    try:
        if strict:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
            except FileNotFoundError:
                raise FileError("없는 파일입니다") from None
            except OSError:
                raise FileError("파일이 아니거나 심볼릭 링크입니다 (링크는 여기서 받을 수 없습니다)") from None
        else:
            target = resolve(root, rel)                  # 공용 영역: 영역 안을 가리키는 링크는 따라간다
            if not target.is_file():
                raise FileError("파일이 아닙니다")
            fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
    finally:
        os.close(dfd)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise FileError("파일이 아닙니다")
    return os.fdopen(fd, "rb"), name, st.st_size
