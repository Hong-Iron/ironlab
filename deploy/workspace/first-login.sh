# 첫 로그인 때 한 번: 기본 셸 설정 파일을 홈에 놓고 안내를 보여 준다.
# 로그인한 사용자 권한으로 실행된다 (컨테이너의 root 는 사용자 홈에 쓸 수 없다).
if [ -n "${HOME:-}" ] && [ -w "$HOME" ] && [ ! -e "$HOME/.ironlab-init" ]; then
    for _f in .bashrc .profile .bash_logout; do
        [ -e "$HOME/$_f" ] || cp "/etc/skel/$_f" "$HOME/$_f" 2>/dev/null
    done
    unset _f
    rm -f "$HOME/.ironlab-welcome"       # 예전 버전이 남긴 표시 파일
    : > "$HOME/.ironlab-init"
    [ -t 1 ] && cat /etc/ironlab-welcome 2>/dev/null
fi
:
