# Helpers for recipes/<NAME>/build.sh (sourced by them; bash).
#
#   with_progress <what> <log> <command...>
#       Runs the command with its output in <log> and, while it runs, prints a line every minute:
#           build.sh: compiling 45% at 12:31 (23 min)
#       The percentage is the last "[ NN%]" that cmake's Makefiles wrote into the log. A build.sh may define
#       progress_hint() (<log> -> one line of text) after sourcing this file, to describe the progress otherwise.
#       The command runs in the foreground, so Ctrl-C stops it as before. Returns the command's exit status.
#
#   missing_libs <prefix>
#       Prints "<file>: <library>" for each shared library that the ELF files under <prefix> need and ldd does
#       not find, with the rule of `prod doctor`: a library lying in the same directory as the file is not
#       missing (Geant4's libraries have no RPATH and find each other through LD_LIBRARY_PATH).
#
#   copy_hint <prefix>
#       Prints the rsync command that copies the prefix to the products server.

progress_hint() { # the last cmake percentage in the log, e.g. "45%"
    grep -o '^\[ *[0-9]*%\]' "$1" 2>/dev/null | tail -1 | tr -d '[] '
}

with_progress() {
    local what="$1" log="$2"
    shift 2
    local t0 rc=0 reporter
    t0="$(date +%s)"
    (
        # not build.sh's set -e: a log with nothing to report yet (grep finds no match) must not end the reporter
        set +e +o pipefail
        # stopped by the kill below; the sleep goes with it
        trap 'kill $! 2>/dev/null; exit 0' TERM
        while :; do
            sleep 60 & wait $!
            kill -0 $$ 2>/dev/null || exit 0 # build.sh itself was stopped
            hint="$(progress_hint "$log")"
            echo "build.sh: $what${hint:+ $hint} at $(date +%H:%M) ($(( ($(date +%s) - t0) / 60 )) min)"
        done
    ) &
    reporter=$!
    "$@" > "$log" 2>&1 || rc=$?
    kill "$reporter" 2>/dev/null || true
    wait "$reporter" 2>/dev/null || true
    return "$rc"
}

missing_libs() {
    find "$1" -type f \( -name '*.so*' -o -perm -u+x \) -exec sh -c '
        for f; do
            head -c4 "$f" | grep -q ELF || continue
            ldd "$f" 2>/dev/null | sed -n "s|^\t\(.*\) => not found|\1|p" | while read -r lib; do
                [ -e "$(dirname "$f")/$lib" ] || echo "$f: $lib"
            done
        done
        exit 0' _ {} + | sort -u
}

copy_hint() {
    # The destination is the server's real path: rsync 3.2.7-1.el9_8 as the receiving side refuses a destination
    # that goes through a symlink ("Too many levels of symbolic links"), and /home/cupsoft/Products is one.
    log "  rsync -a $1/ <server>:<$1 with symlinks resolved on the server>/"
    log "  (find that path with: ssh <server> readlink -f $1)"
}
