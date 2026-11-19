#!/bin/bash
# Install the prod tool from this checkout into <Products>/.prod.
#
#   ./install.sh [--products DIR] [--role client|server] [--server SPEC] [--force] [--keep N]
#                [--keep-legacy-setup | --replace-legacy-setup]
#
# - Copies the tool to <Products>/.prod/versions/<version>/ and switches the 'current' symlink atomically.
# - Creates <Products>/.prod/etc/prod.conf only if it does not exist yet.
# - Links ~/setup_prod.sh, ~/setup_prod.csh and ~/.local/bin/prod to the current tool. A real
#   ~/setup_prod.sh (the legacy one) is moved to ~/setup_prod.sh.pre-prod first.
# - Keeps the newest N tool versions (default 3).
# - --keep-legacy-setup leaves ~/setup_prod.sh alone and creates no ~/setup_prod.csh; only ~/.local/bin/prod
#   is linked. This is the DEFAULT where role is 'server': nodes that still use the legacy
#   install_products.sh copy the server's ~/setup_prod* (scp follows symlinks), so those files must stay
#   legacy until no node uses install_products.sh any more. Then run install.sh --replace-legacy-setup once.
#   Meanwhile the new setup script is <Products>/.prod/current/share/setup_prod.sh.
set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PRODUCTS="${HOME}/Products"
ROLE=""
SERVER=""
FORCE=0
KEEP=3
VERSION_NAME=""
KEEP_LEGACY_SETUP=""
REQUIRED_PACKAGES=(environment-modules rsync openssh-clients python3 python3-pyyaml git)

die() { echo "install.sh: $*" >&2; exit 1; }

usage() {
    sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    cat <<'EOF'
Options:
  --products DIR    Products directory (default: $HOME/Products). It must exist and belong to you.
  --role ROLE       client or server; needed only when prod.conf does not exist yet
  --server SPEC     products server for a client: [user@]host:/path or /path
  --force           reinstall a version that is already installed
  --keep N          number of tool versions to keep (default 3)
  --version-name V  install under this name instead of 'git describe' (development and tests only)
  --keep-legacy-setup
                    do not touch ~/setup_prod.sh and do not create ~/setup_prod.csh
                    (default where role is 'server'; see above)
  --replace-legacy-setup
                    link ~/setup_prod.sh to the new one even where role is 'server' (end of the transition)
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --products) PRODUCTS="${2:?}"; shift 2 ;;
        --role) ROLE="${2:?}"; shift 2 ;;
        --server) SERVER="${2:?}"; shift 2 ;;
        --force) FORCE=1; shift ;;
        --keep) KEEP="${2:?}"; shift 2 ;;
        --version-name) VERSION_NAME="${2:?}"; shift 2 ;;
        --keep-legacy-setup) KEEP_LEGACY_SETUP=1; shift ;;
        --replace-legacy-setup) KEEP_LEGACY_SETUP=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done

# --------------------------------------------------------------------------- checks

[ "$(id -u)" -ne 0 ] || die "do not run as root; run as the owner of the Products directory (cupsoft)"
case "$PRODUCTS" in
    /*) ;;
    *) die "--products must be an absolute path" ;;
esac
[[ "$PRODUCTS" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "Products path may use only [A-Za-z0-9._/-]: $PRODUCTS"
PRODUCTS="${PRODUCTS%/}"
[ -d "$PRODUCTS" ] || die "$PRODUCTS does not exist; create it first (it may be a symlink to a local disk)"
owner="$(stat -L -c %U -- "$PRODUCTS")"
[ "$owner" = "$(id -un)" ] || die "$PRODUCTS belongs to $owner, not to $(id -un)"
[[ "$KEEP" =~ ^[1-9][0-9]*$ ]] || die "--keep must be a positive number"

if command -v rpm >/dev/null 2>&1; then
    missing=()
    for p in "${REQUIRED_PACKAGES[@]}"; do
        rpm -q "$p" >/dev/null 2>&1 || missing+=("$p")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        die "missing system packages: ${missing[*]} (install them with: sudo dnf install ${missing[*]})"
    fi
fi

if [ -z "$VERSION_NAME" ]; then
    VERSION_NAME="$(git -C "$REPO" describe --tags --dirty --always 2>/dev/null)" \
        || die "$REPO is not a git checkout; cannot name the version (use --version-name for development)"
fi
[[ "$VERSION_NAME" =~ ^[A-Za-z0-9._+-]+$ ]] || die "invalid version name: $VERSION_NAME"
if ! [[ "$VERSION_NAME" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "install.sh: warning: '$VERSION_NAME' is not a release tag (vX.Y.Z); nodes should run tagged versions" >&2
fi

PROD="$PRODUCTS/.prod"
VERSIONS="$PROD/versions"
TARGET="$VERSIONS/$VERSION_NAME"
if [ -e "$TARGET" ] && [ "$FORCE" -ne 1 ]; then
    die "$VERSION_NAME is already installed in $VERSIONS (use --force to reinstall)"
fi

if [ ! -e "$PROD/etc/prod.conf" ]; then
    if [ -z "$ROLE" ]; then
        if [ -t 0 ]; then
            read -r -p "role of this node (client/server): " ROLE
        else
            die "$PROD/etc/prod.conf does not exist; give --role (and --server for a client)"
        fi
    fi
    if [ "$ROLE" = "client" ] && [ -z "$SERVER" ]; then
        if [ -t 0 ]; then
            read -r -p "products server ([user@]host:/path): " SERVER
        else
            die "a client needs --server"
        fi
    fi
fi

unlock_and_remove() {  # tool versions are installed read-only
    [ -e "$1" ] || return 0
    find "$1" -type d -exec chmod u+w {} +
    rm -rf -- "$1"
}

# --------------------------------------------------------------------------- copy

mkdir -p "$VERSIONS" "$PROD/etc" "$PROD/state"
TMP="$VERSIONS/.$VERSION_NAME.tmp.$$"
trap 'unlock_and_remove "$TMP"' EXIT
mkdir "$TMP" "$TMP/share"
cp -r "$REPO/bin" "$REPO/lib" "$TMP/"
for d in modulefiles recipes packages; do
    mkdir "$TMP/share/$d"
    cp -r "$REPO/$d/." "$TMP/share/$d/"
    rm -f "$TMP/share/$d/.gitkeep"
done
[ ! -e "$REPO/postinstall.sh" ] || cp "$REPO/postinstall.sh" "$TMP/share/"
for f in setup_prod.sh setup_prod.csh; do
    sed "s|@PRODUCTS@|$PRODUCTS|g" "$REPO/shell/$f" > "$TMP/share/$f"
done
find "$TMP" -name __pycache__ -prune -exec rm -rf {} +
echo "$VERSION_NAME" > "$TMP/VERSION"
chmod -R a-w,go+rX "$TMP"

if [ -e "$TARGET" ]; then
    unlock_and_remove "$TARGET"
fi
mv -T -- "$TMP" "$TARGET"
trap - EXIT

ln -s "versions/$VERSION_NAME" "$PROD/.current.tmp.$$"
mv -T -f -- "$PROD/.current.tmp.$$" "$PROD/current"

# --------------------------------------------------------------------------- configuration

PY=(/usr/bin/python3 -I -c)
if [ ! -e "$PROD/etc/prod.conf" ]; then
    "${PY[@]}" '
import sys
sys.path.insert(0, sys.argv[1])
from prod import config
sys.stdout.buffer.write(config.dumps(sys.argv[2], sys.argv[3] or None))
' "$TARGET/lib" "$ROLE" "$SERVER" > "$PROD/etc/prod.conf.tmp.$$" \
        || { rm -f "$PROD/etc/prod.conf.tmp.$$"; die "invalid --role or --server"; }
    mv -- "$PROD/etc/prod.conf.tmp.$$" "$PROD/etc/prod.conf"
    echo "install.sh: created $PROD/etc/prod.conf"
fi

# --------------------------------------------------------------------------- links in $HOME

link() {  # link TARGET NAME: make NAME a symlink to TARGET; keep a real file as NAME.pre-prod
    local target="$1" name="$2" backup
    if [ -e "$name" ] && [ ! -L "$name" ]; then
        backup="$name.pre-prod"
        [ ! -e "$backup" ] || backup="$backup.$(date +%Y%m%d%H%M%S)"
        mv -- "$name" "$backup"
        echo "install.sh: moved the existing $name to $backup"
    fi
    ln -s -- "$target" "$name.tmp.$$"
    mv -T -f -- "$name.tmp.$$" "$name"
}
if [ -z "$KEEP_LEGACY_SETUP" ]; then
    # default: keep the legacy setup script where this node is the products server
    node_role="$("${PY[@]}" '
import sys
sys.path.insert(0, sys.argv[1])
from prod import config
print(config.load(config.Paths(sys.argv[2]), env={}).role)
' "$TARGET/lib" "$PRODUCTS")" || die "cannot read $PROD/etc/prod.conf"
    [ "$node_role" = "server" ] && KEEP_LEGACY_SETUP=1 || KEEP_LEGACY_SETUP=0
fi
mkdir -p "$HOME/.local/bin"
if [ "$KEEP_LEGACY_SETUP" -eq 1 ]; then
    echo "install.sh: --keep-legacy-setup: ~/setup_prod.sh is left as it is; the new one is" \
         "$PROD/current/share/setup_prod.sh"
else
    link "$PROD/current/share/setup_prod.sh" "$HOME/setup_prod.sh"
    link "$PROD/current/share/setup_prod.csh" "$HOME/setup_prod.csh"
fi
link "$PROD/current/bin/prod" "$HOME/.local/bin/prod"

# --------------------------------------------------------------------------- old versions

mapfile -t old < <(cd "$VERSIONS" && ls -1t | grep -v '^\.' | grep -vxF "$VERSION_NAME" | tail -n +"$KEEP")
for v in "${old[@]}"; do
    unlock_and_remove "$VERSIONS/$v"
    echo "install.sh: removed old tool version $v"
done

"${PY[@]}" '
import sys
sys.path.insert(0, sys.argv[1])
from prod import config, state
state.record(config.Paths(sys.argv[2]), sys.argv[3], "install", result="ok")
' "$TARGET/lib" "$PRODUCTS" "$VERSION_NAME"

echo "install.sh: installed $("$PROD/current/bin/prod" --version) in $PROD"
echo "install.sh: running prod doctor --quick"
"$PROD/current/bin/prod" doctor --quick || echo "install.sh: prod doctor reported problems (see above); the tool is installed"
