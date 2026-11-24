import io
import os

import pytest

from ironlab import files as fs


@pytest.fixture
def user(settings):
    (settings.users_root / "alice").mkdir()
    (settings.scratch_root / "alice").mkdir()
    return {"name": "alice", "uid": 20001, "role": "user", "disk_gb": 1}


def test_resolve_blocks_traversal(settings, user):
    root = settings.users_root / "alice"
    for bad in ("../bob", "a/../../bob", "../../etc/passwd"):
        with pytest.raises(fs.FileError):
            fs.resolve(root, bad)
    # 앞의 / 는 영역 루트 기준으로 해석된다
    assert fs.resolve(root, "/x") == (root / "x").resolve()


def test_symlink_escape_blocked(settings, user, tmp_path):
    root = settings.users_root / "alice"
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    os.symlink(secret, root / "link")
    with pytest.raises(fs.FileError):
        fs.open_for_download(settings, user, "home", "link")
    # 링크 자체는 지울 수 있고, 대상은 남는다
    fs.delete_path(settings, user, "home", "link", recursive=False)
    assert not (root / "link").exists() and secret.exists()


def test_upload_quota(settings, user):
    fs.save_upload(settings, user, "home", "", "a.bin", io.BytesIO(b"x" * 1000))
    assert (settings.users_root / "alice" / "a.bin").stat().st_size == 1000
    small = dict(user, disk_gb=0)
    with pytest.raises(fs.QuotaExceeded):
        fs.save_upload(settings, small, "home", "", "b.bin", io.BytesIO(b"x" * 10))
    assert not (settings.users_root / "alice" / "b.bin").exists()
    assert not list((settings.users_root / "alice").glob(".*uploading*"))


def test_shared_is_read_only_for_users(settings, user):
    with pytest.raises(fs.FileError):
        fs.save_upload(settings, user, "shared", "models", "m.bin", io.BytesIO(b"x"))
    admin = dict(user, role="admin")
    fs.save_upload(settings, admin, "shared", "models", "m.bin", io.BytesIO(b"x"))
    assert (settings.shared_root / "models" / "m.bin").exists()


def test_mkdir_list_delete(settings, user):
    fs.make_dir(settings, user, "home", "", "proj")
    fs.save_upload(settings, user, "home", "proj", "f.txt", io.BytesIO(b"hi"))
    listing = fs.list_dir(settings.users_root / "alice", "proj")
    assert [e["name"] for e in listing["entries"]] == ["f.txt"]
    with pytest.raises(fs.FileError):
        fs.delete_path(settings, user, "home", "proj", recursive=False)
    fs.delete_path(settings, user, "home", "proj", recursive=True)
    assert not (settings.users_root / "alice" / "proj").exists()
    with pytest.raises(fs.FileError):
        fs.delete_path(settings, user, "home", "", recursive=True)


def test_bad_names(settings, user):
    for bad in ("", ".", "..", "a/b"):
        with pytest.raises(fs.FileError):
            fs.make_dir(settings, user, "home", "", bad)


# ── 서버는 root 로 돌고 홈 안은 사용자가 마음대로 바꿀 수 있다: 링크로 영역 밖을 건드리게 만들 수 없어야 한다 ──

def test_upload_does_not_write_through_a_planted_temp_symlink(settings, user, tmp_path):
    """예전에는 임시 파일 이름(.이름.uploading)으로 링크를 미리 만들어 두면 서버가 링크 너머를 덮어썼다."""
    root = settings.users_root / "alice"
    victim = tmp_path / "host-file"
    victim.write_text("ORIGINAL")
    os.symlink(victim, root / ".evil.uploading")
    fs.save_upload(settings, user, "home", "", "evil", io.BytesIO(b"from user"))
    assert victim.read_text() == "ORIGINAL"
    assert (root / "evil").read_bytes() == b"from user"


def test_upload_replaces_a_symlink_instead_of_following_it(settings, user, tmp_path):
    root = settings.users_root / "alice"
    victim = tmp_path / "host-file"
    victim.write_text("ORIGINAL")
    os.symlink(victim, root / "notes.txt")
    fs.save_upload(settings, user, "home", "", "notes.txt", io.BytesIO(b"new"))
    assert victim.read_text() == "ORIGINAL"
    assert not (root / "notes.txt").is_symlink() and (root / "notes.txt").read_bytes() == b"new"


def test_symlinked_folder_cannot_be_used_to_reach_outside(settings, user, tmp_path):
    root = settings.users_root / "alice"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("host secret")
    (outside / "keep").mkdir()
    os.symlink(outside, root / "link")
    for attempt in (
        lambda: fs.list_dir(root, "link"),
        lambda: fs.open_for_download(settings, user, "home", "link/secret.txt"),
        lambda: fs.save_upload(settings, user, "home", "link", "x.txt", io.BytesIO(b"x")),
        lambda: fs.make_dir(settings, user, "home", "link", "newdir"),
        lambda: fs.delete_path(settings, user, "home", "link/secret.txt", recursive=False),
        lambda: fs.delete_path(settings, user, "home", "link/keep", recursive=True),
    ):
        with pytest.raises(fs.FileError):
            attempt()
    assert sorted(p.name for p in outside.iterdir()) == ["keep", "secret.txt"]
    # 링크 자체를 지우는 것은 되고, 가리키던 폴더는 그대로다
    fs.delete_path(settings, user, "home", "link", recursive=True)
    assert not (root / "link").is_symlink() and (outside / "secret.txt").exists()


def test_recursive_delete_does_not_follow_links_inside(settings, user, tmp_path):
    root = settings.users_root / "alice"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me")
    (root / "proj").mkdir()
    os.symlink(outside, root / "proj" / "data")
    fs.delete_path(settings, user, "home", "proj", recursive=True)
    assert not (root / "proj").exists() and (outside / "precious.txt").read_text() == "keep me"


def test_download_opens_only_regular_files(settings, user):
    root = settings.users_root / "alice"
    (root / "a.txt").write_text("hello")
    f, name, size = fs.open_for_download(settings, user, "home", "a.txt")
    with f:
        assert (f.read(), name, size) == (b"hello", "a.txt", 5)
    os.mkfifo(root / "pipe")            # 읽으려 하면 영영 멈추는 파일: 열지 않고 거절해야 한다
    (root / "dir").mkdir()
    for bad in ("pipe", "dir", "missing", ""):
        with pytest.raises(fs.FileError):
            fs.open_for_download(settings, user, "home", bad)


def test_shared_area_follows_links_that_stay_inside(settings, user, tmp_path):
    """HF 캐시는 snapshots/… → blobs/… 링크로 이뤄져 있다. 관리자만 쓰는 공용 영역에서는 그대로 받을 수 있어야 한다."""
    hub = settings.shared_root / "models" / "hub"
    (hub / "blobs").mkdir(parents=True)
    (hub / "snap").mkdir()
    (hub / "blobs" / "abc").write_text("weights")
    os.symlink("../blobs/abc", hub / "snap" / "model.bin")
    f, name, size = fs.open_for_download(settings, user, "shared", "models/hub/snap/model.bin")
    with f:
        assert f.read() == b"weights"
    os.symlink(tmp_path, settings.shared_root / "escape")      # 영역 밖을 가리키는 링크는 여전히 막는다
    with pytest.raises(fs.FileError):
        fs.list_dir(settings.shared_root, "escape", strict=False)


def test_uploaded_files_are_handed_to_the_user(settings, user, monkeypatch):
    """실제 서버(root)에서는 올린 파일·폴더가 사용자 소유가 된다. 경로가 아니라 연 파일에 대해 바꾼다."""
    if os.geteuid() != 0:
        pytest.skip("root 에서만 확인 가능")
    monkeypatch.setattr(settings, "mock", False)
    fs.save_upload(settings, user, "home", "", "mine.txt", io.BytesIO(b"x"))
    fs.make_dir(settings, user, "home", "", "mydir")
    root = settings.users_root / "alice"
    assert (root / "mine.txt").stat().st_uid == 20001 and (root / "mydir").stat().st_uid == 20001
