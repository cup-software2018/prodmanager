#!/bin/bash
# Install the system packages of prodmanager. Run as root:
#
#   sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh [--group G]... [--check]
#
# - Reads the package lists in <Products>/packages.
# - Enables the repositories the groups need (crb, epel).
# - Installs each group's packages in one dnf transaction; if that fails, retries them one by one so that
#   only the packages that really fail are reported (one bad name no longer drops a whole group).
#   'optional' packages are tried separately and only warned about.
# - Prints a result table, logs everything to /var/log/prodmanager-postinstall.log.
# - Does not run 'dnf update'; updating the system is the administrator's separate decision.
# - --check installs nothing: it shows what is installed, what would be installed, and what is not
#   available in the enabled repositories.
#
# Exit code: 0 if every required package is installed, 1 otherwise.
#
# For tests only: PROD_DNF / PROD_RPM replace the dnf / rpm commands, PROD_POSTINSTALL_LOG the log file,
# and PROD_ALLOW_NONROOT=1 skips the root check.
set -u

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DNF="${PROD_DNF:-dnf}"
RPM="${PROD_RPM:-rpm}"
LOG="${PROD_POSTINSTALL_LOG:-/var/log/prodmanager-postinstall.log}"
GROUPS_WANTED=()
CHECK=0

die() { echo "postinstall.sh: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --group) GROUPS_WANTED+=("${2:?}"); shift 2 ;;
        --check) CHECK=1; shift ;;
        -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

if [ "$(id -u)" -ne 0 ] && [ "${PROD_ALLOW_NONROOT:-0}" != 1 ]; then
    die "run as root (sudo)"
fi

# Installed tool: <Products>/.prod/versions/<v>/share/postinstall.sh, with the package lists in <Products>/packages
# (edited on the server; on a client, the server's, fetched by prodman sync).
# Repository checkout: <repo>/postinstall.sh with <repo>/packages.
case "$HERE" in
    */.prod/versions/*/share)
        PRODUCTS="${HERE%%/.prod/versions/*}"; LIB="$HERE/../lib"; TOOL_ROOT="$HERE/.."
        PKG_DIR="$PRODUCTS/packages" ;;
    *)
        PRODUCTS=""; LIB="$HERE/lib"; TOOL_ROOT="$HERE"
        PKG_DIR="$HERE/packages" ;;
esac
[ -d "$LIB/prod" ] || die "cannot find lib/prod for $HERE"
[ -d "$PKG_DIR" ] || die "no package lists in $PKG_DIR (on a client, run 'prodman sync <vlist>' first)"
TOOL="$(cat "$TOOL_ROOT/VERSION" 2>/dev/null || echo "checkout")"

py() { /usr/bin/python3 -I -B -c 'import sys; sys.path.insert(0, sys.argv[1]); from prod.packages import _main; sys.exit(_main(sys.argv[2:]))' "$LIB" "$@"; }

PLAN="$(py plan "$PKG_DIR" "${GROUPS_WANTED[@]}")" || die "cannot read the package lists in $PKG_DIR"

mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
echo "=== postinstall.sh $TOOL on $(hostname -s) at $(date -Is)$([ "$CHECK" -eq 1 ] && echo ' (check only)')"

installed() { "$RPM" -q "$1" >/dev/null 2>&1; }
available() { "$DNF" -q repoquery "$1" 2>/dev/null | grep -q .; }

# --------------------------------------------------------------------------- repositories

need_repos=" $(printf '%s\n' "$PLAN" | cut -d'|' -f2 | tr '\n' ' ') "
if [ "$CHECK" -eq 0 ]; then
    case "$need_repos" in *" crb "*)
        echo "--- enabling crb"
        "$DNF" -y config-manager --set-enabled crb || echo "warning: could not enable crb" ;;
    esac
    case "$need_repos" in *" epel "*)
        if ! installed epel-release; then
            echo "--- installing epel-release"
            "$DNF" -y install epel-release || echo "warning: could not install epel-release"
        fi ;;
    esac
fi

# --------------------------------------------------------------------------- groups

any_failed=0
summary=()

while IFS='|' read -r group repos required optional; do
    [ -n "$group" ] || continue
    req=()
    for p in $required; do req+=("${p//%20/ }"); done
    opt=($optional)
    echo "--- group $group: ${#req[@]} required, ${#opt[@]} optional"
    failed=()
    opt_failed=()

    if [ "$CHECK" -eq 1 ]; then
        for p in "${req[@]}" "${opt[@]}"; do
            case "$p" in @*) echo "  group     $p (not checked)"; continue ;; esac
            if installed "$p"; then echo "  installed $p"
            elif available "$p"; then echo "  INSTALL   $p"
            else
                echo "  MISSING   $p (not in the enabled repositories)"
                case " ${opt[*]} " in *" $p "*) opt_failed+=("$p") ;; *) failed+=("$p") ;; esac
            fi
        done
    else
        if [ "${#req[@]}" -gt 0 ] && ! "$DNF" -y install "${req[@]}"; then
            echo "  group install failed; retrying the packages one by one"
            for p in "${req[@]}"; do
                "$DNF" -y install "$p" || failed+=("$p")
            done
        fi
        for p in "${opt[@]}"; do
            "$DNF" -y install "$p" || opt_failed+=("$p")
        done
    fi

    result=ok
    if [ "${#failed[@]}" -gt 0 ]; then result=failed; any_failed=1; fi
    summary+=("$(printf '  %-8s %-7s failed: %s   optional failed: %s' "$group" "$result" \
        "${failed[*]:--}" "${opt_failed[*]:--}")")
done <<< "$PLAN"

echo "=== result$([ "$CHECK" -eq 1 ] && echo ' (check only: nothing was installed)')"
printf '%s\n' "${summary[@]}"

exit "$any_failed"
