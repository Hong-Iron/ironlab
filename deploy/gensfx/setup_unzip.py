#!/usr/bin/env python3
"""setup_woosh.sh 가 쓰는 압축 풀기. 서버에 unzip 이 없을 수 있어 파이썬으로 푼다.

    setup_unzip.py <파일.zip> <풀 폴더> [이 글자로 시작하는 항목만]

항목 이름이 / 로 시작하거나 .. 를 품으면 풀지 않고 멈춘다 (풀 폴더 밖에 쓰는 것을 막는다).
"""

import os
import sys
import zipfile


def main() -> None:
    if len(sys.argv) not in (3, 4):
        sys.exit("사용법: setup_unzip.py <파일.zip> <풀 폴더> [앞글자]")
    src, dest = sys.argv[1], os.path.abspath(sys.argv[2])
    prefix = sys.argv[3] if len(sys.argv) == 4 else ""
    with zipfile.ZipFile(src) as z:
        names = [n for n in z.namelist() if n.startswith(prefix)]
        for n in names:
            parts = n.replace("\\", "/").split("/")
            if n.startswith("/") or ".." in parts:
                sys.exit(f"[setup] 실패: 압축 안의 이름이 풀 폴더 밖을 가리킵니다 ({n!r})")
        for n in names:
            z.extract(n, dest)
    print(f"[setup] {os.path.basename(src)}: {len(names)}개 항목을 {dest} 에 풀었습니다")


if __name__ == "__main__":
    main()
