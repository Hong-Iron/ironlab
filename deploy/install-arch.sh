#!/usr/bin/env bash
# 예전 이름. 이제 deploy/install.sh 가 Ubuntu·Debian·Arch 를 모두 맡는다.
# 처음 이 스크립트로 설치한 서버의 동작(큰 파티션 /home 위에 Docker·/srv, Tailscale 로 공개)을 그대로 지키려고
# BIG 과 ACCESS 의 기본값만 정해서 넘긴다.
#
#   sudo ADMIN=admin TS_LOGIN=you@github bash deploy/install-arch.sh
exec env BIG="${BIG-/home}" ACCESS="${ACCESS:-tailscale}" bash "$(dirname "${BASH_SOURCE[0]}")/install.sh" "$@"
