#!/bin/bash
# Install the prod tool from this checkout into <Products>/.prod.
#
#   ./install.sh [--products_dir DIR] [--role client|server] [--server SPEC] [--force]
#
# - Copies the tool to <Products>/.prod/versions/<version>/, switches the 'current' symlink atomically and
#   removes every other tool version.
# - Creates <Products>/.prod/etc/prod.conf only if it does not exist yet.
# - On a server, puts the build scripts and the example recipes into <Products>/build-recipes, where versions are
#   built. prod does not manage that directory: the scripts are replaced by this version's, recipes (V*.yml,
#   *.requirements.txt) are only added when missing, and nothing is removed.
# - On a server, puts the system package lists into <Products>/packages, only those that are missing: they are
#   edited there (clients get them with prodman sync). Also creates <Products>/vlist/drafts for the drafts.
# - Links ~/setup_prod.sh, ~/setup_prod.csh and ~/.local/bin/prodman to the current tool. A real file in their
#   place is moved to <name>.bak first.
set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PRODUCTS="${HOME}/Products"
ROLE=""
SERVER=""
FORCE=0
VERSION_NAME=""
REQUIRED_PACKAGES=(environment-modules rsync openssh-clients python3 python3-pyyaml git)

die() { echo "install.sh: $*" >&2; exit 1; }

usage() {
    sed -n '2,/^[^#]/p' "${BASH_SOURCE[0]}" | grep '^#' | sed 's/^# \{0,1\}//'
    cat <<'EOF'
Options:
  --products_dir DIR  Products directory (default: $HOME/Products). It must exist and belong to you.
  --role ROLE         client or server; needed only when prod.conf does not exist yet
  --server SPEC       products server for a client: [user@]host:/path or /path
  --force             reinstall a version that is already installed
  --version-name V    install under this name instead of 'git describe' (development and tests only)
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --products_dir) PRODUCTS="${2:?}"; shift 2 ;;
        --role) ROLE="${2:?}"; shift 2 ;;
        --server) SERVER="${2:?}"; shift 2 ;;
        --force) FORCE=1; shift ;;
        --version-name) VERSION_NAME="${2:?}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done

# --------------------------------------------------------------------------- checks

[ "$(id -u)" -ne 0 ] || die "do not run as root; run as the owner of the Products directory (cupsoft)"
case "$PRODUCTS" in
    /*) ;;
    *) die "--products_dir must be an absolute path" ;;
esac
[[ "$PRODUCTS" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "Products path may use only [A-Za-z0-9._/-]: $PRODUCTS"
PRODUCTS="${PRODUCTS%/}"
[ -d "$PRODUCTS" ] || die "$PRODUCTS does not exist; create it first (it may be a symlink to a local disk)"
owner="$(stat -L -c %U -- "$PRODUCTS")"
[ "$owner" = "$(id -un)" ] || die "$PRODUCTS belongs to $owner, not to $(id -un)"

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
if ! [[ "$VERSION_NAME" =~ ^v[0-9]+\.[0-9]+$ ]]; then
    echo "install.sh: warning: '$VERSION_NAME' is not a release tag (vX.Y); nodes should run tagged versions" >&2
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
for d in modulefiles; do
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
# The Products path as given, symlinks not resolved: prod writes it into modulefiles.
echo "$PRODUCTS" > "$TMP/PRODUCTS"
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

# --------------------------------------------------------------------------- build recipes (server only)

node_role="$("${PY[@]}" '
import sys
sys.path.insert(0, sys.argv[1])
from prod import config
print(config.load(config.Paths(sys.argv[2]), env={}).role)
' "$TARGET/lib" "$PRODUCTS")" || die "cannot read $PROD/etc/prod.conf"
if [ "$node_role" = "server" ]; then
    mkdir -p "$PRODUCTS/build-recipes"
    # the real path: rsync 3.2.7 (EL9) refuses a destination that goes through a symlink, as ~/Products often is
    dest="$(cd -P -- "$PRODUCTS/build-recipes" && pwd)"
    rsync -a --exclude='V*.yml' --exclude='*.requirements.txt' --exclude=__pycache__ --exclude=.gitkeep \
        "$REPO/build-recipes/" "$dest/"
    rsync -a --ignore-existing --include='*/' --include='V*.yml' --include='*.requirements.txt' --exclude='*' \
        "$REPO/build-recipes/" "$dest/"
    echo "install.sh: build scripts updated in $PRODUCTS/build-recipes (the recipes there are kept)"
    mkdir -p "$PRODUCTS/packages"
    rsync -a --ignore-existing --include='*.yml' --exclude='*' "$REPO/packages/" "$(cd -P -- "$PRODUCTS/packages" && pwd)/"
    echo "install.sh: system package lists in $PRODUCTS/packages (existing ones are kept)"
    mkdir -p "$PRODUCTS/vlist/drafts"
fi

# --------------------------------------------------------------------------- links in $HOME

link() {  # link TARGET NAME: make NAME a symlink to TARGET; keep a real file as NAME.bak
    local target="$1" name="$2" backup
    if [ -e "$name" ] && [ ! -L "$name" ]; then
        backup="$name.bak"
        [ ! -e "$backup" ] || backup="$backup.$(date +%Y%m%d%H%M%S)"
        mv -- "$name" "$backup"
        echo "install.sh: moved the existing $name to $backup"
    fi
    ln -s -- "$target" "$name.tmp.$$"
    mv -T -f -- "$name.tmp.$$" "$name"
}
mkdir -p "$HOME/.local/bin"
link "$PROD/current/share/setup_prod.sh" "$HOME/setup_prod.sh"
link "$PROD/current/share/setup_prod.csh" "$HOME/setup_prod.csh"
link "$PROD/current/bin/prodman" "$HOME/.local/bin/prodman"
# the tool was called prod before: drop that link if it points into this Products directory
if [ -L "$HOME/.local/bin/prod" ] && [[ "$(readlink "$HOME/.local/bin/prod")" == "$PROD/"* ]]; then
    rm -f -- "$HOME/.local/bin/prod"
fi

# --------------------------------------------------------------------------- other versions

mapfile -t old < <(cd "$VERSIONS" && ls -1 | grep -v '^\.' | grep -vxF "$VERSION_NAME")
for v in "${old[@]}"; do
    unlock_and_remove "$VERSIONS/$v"
    echo "install.sh: removed tool version $v"
done

"${PY[@]}" '
import sys
sys.path.insert(0, sys.argv[1])
from prod import config, state
state.record(config.Paths(sys.argv[2]), sys.argv[3], "install", result="ok")
' "$TARGET/lib" "$PRODUCTS" "$VERSION_NAME"

echo "install.sh: installed $("$PROD/current/bin/prodman" --version) in $PROD"
