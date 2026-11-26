#!/bin/bash
# Build CPython <VERSION> at its final path <Products>/PYTHON/<VERSION> (staging, before `prodman release`) from
# build-recipes/PYTHON/<VERSION>.yml (e.g. V3.14.9.yml) and install the packages listed in
# build-recipes/PYTHON/<VERSION>.requirements.txt.
#
#   build-recipes/PYTHON/build.sh <version> [build|packages|all] [--dry-run] [--jobs N] [--workdir DIR]
#
#   <version> is given without the V: `build.sh 3.14.9` builds build-recipes/PYTHON/V3.14.9.yml into
#   <Products>/PYTHON/V3.14.9 (<VERSION> below is V<version>).
#                           [--products_dir DIR] [--sha256 HASH]
#
#   build      download the recipe's source, ./configure with its options, make, make install, check the
#              standard modules
#   packages   pip install the requirements into the new Python (--dry-run: resolve only, install nothing)
#   all        build, then packages (default)
#
# - Runs in a clean environment (no PYTHONPATH, LD_LIBRARY_PATH, ... from a loaded vlist), and with no user
#   site-packages, so nothing outside the new prefix leaks into it.
# - ./configure gets --prefix, --libdir and LDFLAGS (rpath to <prefix>/lib) from build.sh, and the recipe's options.
# - Refuses a version that is already frozen (it has a manifest), and a non-empty prefix for 'build'.
# - The source, the build tree, the pip cache and the logs go to --workdir (default /tmp/prod-build-python-<VERSION>),
#   never under Products. pip-freeze.txt there is the installed package list.
#
# The binary is not relocatable: build at the final path, on the products server or on a node with the same OS
# (AlmaLinux 9) and the same Products path (then copy it to the server as printed at the end). At the end the
# recipe is saved next to the version, <Products>/PYTHON/<VERSION>.recipe.yml; `prodman release` reads it there.

set -euo pipefail

if [ -z "${_PROD_BUILD_CLEAN:-}" ]; then
    exec env -i HOME="$HOME" USER="${USER:-$(id -un)}" PATH=/usr/bin:/bin LANG=C.UTF-8 TERM="${TERM:-dumb}" \
        _PROD_BUILD_CLEAN=1 bash "$0" "$@"
fi

die() { echo "build.sh: $*" >&2; exit 1; }
log() { echo "build.sh: $*"; }

usage() { sed -n '2,/^[^#]/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/../_common/build-common.sh" # with_progress, missing_libs

progress_hint() { # the phase of the profile-guided make, e.g. "profile run, test 12/43 (2/3)"
    local t
    if grep -q -- '-fprofile-use' "$1"; then
        echo "optimized build (3/3)"
    elif t="$(grep -o '\[ *[0-9]*/[0-9]*\] test_' "$1" | tail -1)" && [ -n "$t" ]; then
        echo "profile run, test $(echo "$t" | tr -dc '0-9/') (2/3)"
    elif grep -q -- '-fprofile-generate' "$1"; then
        echo "instrumented build (1/3)"
    fi
}
ARG=""
STAGE="all"
DRY_RUN=0
JOBS="$(nproc)"
WORKDIR=""
PRODUCTS="/home/cupsoft/Products"
SHA256=""

while [ $# -gt 0 ]; do
    case "$1" in
        build|packages|all) STAGE="$1"; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --jobs) JOBS="${2:?}"; shift 2 ;;
        --workdir) WORKDIR="${2:?}"; shift 2 ;;
        --products_dir) PRODUCTS="${2:?}"; shift 2 ;;
        --sha256) SHA256="${2:?}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        -*) usage >&2; die "unknown option: $1" ;;
        *) [ -z "$ARG" ] || die "only one version"; ARG="$1"; shift ;;
    esac
done

[ -n "$ARG" ] || { usage >&2; die "give the version without the V, e.g. 3.14.9"; }
[[ "$ARG" =~ ^[0-9][A-Za-z0-9._-]*$ ]] || die "the version must look like 3.14.9 (without the V): $ARG"
NAME="V$ARG"
[ "$DRY_RUN" -eq 0 ] || [ "$STAGE" = "packages" ] || die "--dry-run is for the 'packages' stage"

PREFIX="$PRODUCTS/PYTHON/$NAME"
PY="$PREFIX/bin/python3"
RECIPE="$HERE/$NAME.yml"
REQS="$HERE/$NAME.requirements.txt"
WORKDIR="${WORKDIR:-${TMPDIR:-/tmp}/prod-build-python-$NAME}"
case "$WORKDIR" in /*) ;; *) WORKDIR="$PWD/$WORKDIR" ;; esac  # absolute: the checks run in other directories

[ -d "$PRODUCTS" ] || die "$PRODUCTS does not exist"
[ ! -e "$PRODUCTS/PYTHON/$NAME.manifest" ] || die "PYTHON $NAME is already released (it has a manifest); it must not change"
[ -f "$RECIPE" ] || die "no recipe $RECIPE"
[ -f "$REQS" ] || die "no package list $REQS"

umask 022
mkdir -p "$WORKDIR"
read_recipe "$RECIPE" # sets SRC_URL, SRC_TAG, OPTIONS
export PYTHONNOUSERSITE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_CACHE_DIR="$WORKDIR/pip-cache"

build() {
    if [ -d "$PREFIX" ] && [ -n "$(ls -A "$PREFIX")" ]; then
        die "$PREFIX is not empty; remove it first to build again (chmod -R u+w if it was locked)"
    fi
    local src="$WORKDIR/src"
    rm -rf "$src" # configure and make work in the source tree: always a fresh one
    fetch_source "$src"
    if [ -n "$SHA256" ]; then
        [ -n "${SRC_ARCHIVE:-}" ] || die "--sha256 is for a downloaded archive"
        [ "$(sha256sum "$SRC_ARCHIVE" | cut -d' ' -f1)" = "$SHA256" ] || die "sha256 differs from --sha256 $SHA256"
    fi
    cd "$src"
    log "configure with the recipe's options (log: $WORKDIR/configure.log)"
    ./configure --prefix="$PREFIX" --libdir="$PREFIX/lib" "${OPTIONS[@]}" \
        LDFLAGS="-L$PREFIX/lib -Wl,-rpath,$PREFIX/lib" \
        > "$WORKDIR/configure.log" 2>&1 || die "configure failed, see $WORKDIR/configure.log"
    log "make -j$JOBS (profile-guided: this takes a while; log: $WORKDIR/make.log)"
    with_progress make "$WORKDIR/make.log" make -j"$JOBS" || die "make failed, see $WORKDIR/make.log"
    log "make install (log: $WORKDIR/install.log)"
    with_progress "make install" "$WORKDIR/install.log" make install || die "make install failed, see $WORKDIR/install.log"
    ln -sfn python3 "$PREFIX/bin/python" # make install creates only python3; users expect python too
    cd "$WORKDIR"

    log "checking the standard modules"
    "$PY" -I -c '
import importlib, sys
mods = ["ssl", "sqlite3", "ctypes", "bz2", "lzma", "zlib", "readline", "curses", "tkinter", "dbm.gnu",
        "uuid", "decimal", "hashlib", "ensurepip", "zoneinfo"]
bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        bad.append(f"{m}: {e}")
print("python", sys.version.split()[0], sys.prefix)
if bad:
    sys.exit("missing modules:\n  " + "\n  ".join(bad))
' || die "the new Python misses standard modules (a -devel package was missing when configuring?)"
    if ldd "$PY" | grep -q 'not found'; then
        ldd "$PY"
        die "$PY misses shared libraries"
    fi
    log "built $("$PY" -I -c 'import sys; print(sys.version.split()[0])') in $PREFIX"
}

packages() {
    [ -x "$PY" ] || die "$PY does not exist; run the 'build' stage first"
    local args=(-m pip install --prefer-binary -r "$REQS")
    if [ "$DRY_RUN" -eq 1 ]; then
        log "resolving $REQS (dry run: nothing is installed; log: $WORKDIR/pip-dry-run.log)"
        "$PY" -I "${args[@]}" --dry-run 2>&1 | tee "$WORKDIR/pip-dry-run.log"
        return
    fi
    log "installing $REQS (log: $WORKDIR/pip.log)"
    "$PY" -I "${args[@]}" 2>&1 | tee "$WORKDIR/pip.log"
    log "pip check"
    "$PY" -I -m pip check 2>&1 | tee -a "$WORKDIR/pip.log" || log "WARNING: pip check reported problems (see above)"
    "$PY" -I -m pip list --format=freeze > "$WORKDIR/pip-freeze.txt"
    log "$(wc -l < "$WORKDIR/pip-freeze.txt") packages installed; list in $WORKDIR/pip-freeze.txt"
}

case "$STAGE" in
    build) build ;;
    packages) packages ;;
    all) build; packages ;;
esac

[ "$DRY_RUN" -eq 1 ] || ready "$RECIPE" "$PREFIX" "$PRODUCTS"
