# Set up the environment of a released vlist. Source it, do not run it:
#
#     . ~/setup_prod.sh <vlist>
#
# Without <vlist> it lists the releases on this node; in an interactive shell on a terminal it then asks which one
# to load (Enter: none).
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
    local restore_u=0 rc=0 skip=0
    case "$-" in *u*) restore_u=1; set +u ;; esac  # the modules init script is not nounset-safe

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
            local f n desc choice i=0
            local -a names=()
            for f in "$products"/vlist/*.yml; do
                [ -e "$f" ] || continue
                n="${f##*/}"
                names+=("${n%.yml}")
            done
            if [ "${#names[@]}" -eq 0 ]; then
                echo "prod: no releases on this node; install one with 'prodman sync <vlist>'" >&2
                rc=1
            else
                echo "releases on this node:" >&2
                for n in "${names[@]}"; do
                    i=$((i + 1))
                    desc="$(sed -n 's/^description: "\(.*\)"$/\1/p' "$products/vlist/$n.yml")"
                    printf '  %d) %-12s %s\n' "$i" "$n" "$desc" >&2
                done
                case "$-" in *i*) [ -t 0 ] && choice="?" ;; esac
                if [ "$choice" = "?" ]; then
                    read -r -p "choose a release (number or name, Enter: none): " choice
                    if [ -z "$choice" ]; then
                        skip=1
                    elif [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#names[@]}" ]; then
                        name="${names[$((choice - 1))]}"
                    else
                        name="$choice"
                    fi
                else
                    echo "usage: . ~/setup_prod.sh <vlist>" >&2
                    rc=1
                fi
            fi
        fi
        if [ "$rc" -eq 0 ] && [ "$skip" -eq 0 ] && [ ! -f "$products/vlist/$name.yml" ]; then
            if [ -f "$HOME/.prod-local/vlist/$name.yml" ]; then
                echo "prod: local vlists (~/.prod-local) are not supported yet" >&2
            else
                echo "prod: vlist '$name' is not installed on this node; run 'prodman sync $name'" >&2
            fi
            rc=1
        fi
    fi

    if [ "$rc" -eq 0 ] && [ "$skip" -eq 0 ]; then
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
