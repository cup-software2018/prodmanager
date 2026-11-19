# Set up the environment of a released vlist. Source it, do not run it:
#
#     . ~/setup_prod.sh <vlist>
#
# It loads the vlist bundle modulefile `vlist/<vlist>` with environment-modules, which loads every
# product of the vlist in order. Loading another vlist replaces the previous one (module switch).
# Safe to source from scripts that use `set -eu`; prints nothing unless the shell is interactive
# (module runs with MODULES_VERBOSITY=concise: errors and warnings only).
#
# @PRODUCTS@ is replaced with the Products directory by install.sh.

_prod_setup() {
    local products="@PRODUCTS@"
    local name="${1:-}"
    local restore_u=0 rc=0
    case "$-" in *u*) restore_u=1; set +u ;; esac  # the modules init script is not nounset-safe

    # PRODUCTS_DIR was exported by the legacy setup_prod.sh; keep it for scripts that use it.
    export PRODUCTS_DIR="$products"

    if ! type module >/dev/null 2>&1; then
        if [ -r /usr/share/Modules/init/bash ]; then
            . /usr/share/Modules/init/bash
        else
            echo "prod: environment-modules is not installed (/usr/share/Modules/init/bash)" >&2
            rc=1
        fi
    fi

    if [ "$rc" -eq 0 ]; then
        case ":${MODULEPATH:-}:" in
            *":$products/modulefiles:"*) ;;
            *) MODULES_VERBOSITY=concise module use "$products/modulefiles" ;;
        esac

        if [ -z "$name" ]; then
            echo "usage: . ~/setup_prod.sh <vlist>" >&2
            echo "vlists on this node:" >&2
            local f found=0
            for f in "$products"/vlist/*.yml; do
                [ -e "$f" ] || continue
                f="${f##*/}"
                echo "  ${f%.yml}" >&2
                found=1
            done
            [ "$found" -eq 1 ] || echo "  (none; install one with 'prod sync <vlist>')" >&2
            rc=1
        elif [ ! -f "$products/vlist/$name.yml" ]; then
            if [ -f "$HOME/.prod-local/vlist/$name.yml" ]; then
                echo "prod: local vlists (~/.prod-local) are not supported yet" >&2
            else
                echo "prod: vlist '$name' is not installed on this node; run 'prod sync $name'" >&2
            fi
            rc=1
        fi
    fi

    if [ "$rc" -eq 0 ]; then
        local withdrawn="$products/vlist/$name.withdrawn" line
        if [ -f "$withdrawn" ]; then
            while IFS= read -r line; do
                case "$line" in
                    reason:*) echo "prod: warning: vlist $name was withdrawn:${line#reason:}" >&2 ;;
                    replaced_by:*) echo "prod: warning: use${line#replaced_by:} instead" >&2 ;;
                esac
            done < "$withdrawn"
        fi
        if [ "${PROD_VLIST:-}" = "$name" ] && module is-loaded "vlist/$name" >/dev/null 2>&1; then
            :  # already loaded
        elif module is-loaded vlist >/dev/null 2>&1; then
            MODULES_VERBOSITY=concise module switch vlist "vlist/$name" || rc=1
        else
            MODULES_VERBOSITY=concise module load "vlist/$name" || rc=1
        fi
        if [ "$rc" -eq 0 ]; then
            case "$-" in
                *i*)
                    local mods
                    mods="$(sed -n 's/^module load //p' "$products/modulefiles/vlist/$name" | tr '\n' ' ')"
                    echo "prod: vlist $name: ${mods% }" >&2
                    ;;
            esac
        else
            echo "prod: loading vlist $name failed" >&2
        fi
    fi

    [ "$restore_u" -eq 0 ] || set -u
    return "$rc"
}

if _prod_setup "$@"; then
    unset -f _prod_setup
else
    _prod_rc=$?
    unset -f _prod_setup
    eval "unset _prod_rc; return $_prod_rc"
fi
