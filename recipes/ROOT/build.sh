#!/bin/bash
# Build ROOT <root version> against PYTHON V<python version> at its final path
# <Products>/ROOT/V<root version>_<python version> (staging, before `prod release`).
#
#   recipes/ROOT/build.sh <root version> <python version> [configure|build|check|all] [--jobs N]
#                         [--workdir DIR] [--products DIR]
#
#   configure  download the source and run cmake with recipes/ROOT/V<root>_<python>.options; prints the features
#   build      compile and install (cmake --build, then --install), then 'check'
#   check      root-config, a ROOT macro, `import ROOT` in the new Python, missing shared libraries
#   all        configure, then build (default)
#
# - Runs in a clean environment: PATH has only the system and the PYTHON's bin, no PYTHONPATH or LD_LIBRARY_PATH
#   from a loaded vlist. The PYTHON is only read: no bytecode is written into it.
# - Refuses a ROOT version that is already frozen (it has a manifest), and a non-empty prefix for 'build'.
# - The source, the build tree (about 15 GB) and the logs go to --workdir (default
#   /tmp/prod-build-root-<root>_<python>), never under Products.
# - ROOT compiles LLVM/Clang: each job may need 2 GB of memory, so keep --jobs below memory in GB / 2.
#
# The binary is not relocatable: build at the final path, on the products server or on a node with the same OS
# (AlmaLinux 9) and the same Products path (then copy it to the server as printed at the end). At the end the
# recipe is saved next to the version, <Products>/ROOT/V<version>.recipe.yml; `prod release` reads it there.

set -euo pipefail

if [ -z "${_PROD_BUILD_CLEAN:-}" ]; then
    exec env -i HOME="$HOME" USER="${USER:-$(id -un)}" PATH=/usr/bin:/bin LANG=C.UTF-8 TERM="${TERM:-dumb}" \
        _PROD_BUILD_CLEAN=1 bash "$0" "$@"
fi

die() { echo "build.sh: $*" >&2; exit 1; }
log() { echo "build.sh: $*"; }

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/../_common/build-common.sh" # with_progress, missing_libs
ROOTVER=""
PYVER=""
STAGE="all"
JOBS="$(nproc)"
WORKDIR=""
PRODUCTS="/home/cupsoft/Products"

while [ $# -gt 0 ]; do
    case "$1" in
        configure|build|check|all) STAGE="$1"; shift ;;
        --jobs) JOBS="${2:?}"; shift 2 ;;
        --workdir) WORKDIR="${2:?}"; shift 2 ;;
        --products) PRODUCTS="${2:?}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        -*) usage >&2; die "unknown option: $1" ;;
        *) if [ -z "$ROOTVER" ]; then ROOTVER="$1"; elif [ -z "$PYVER" ]; then PYVER="$1"; else die "too many arguments"; fi
           shift ;;
    esac
done

[ -n "$ROOTVER" ] && [ -n "$PYVER" ] || { usage >&2; die "give the ROOT and Python versions, e.g. 6.40.04 3.14.9"; }
[[ "$ROOTVER" =~ ^6\.[0-9]+\.[0-9]+$ ]] || die "ROOT version must look like 6.40.04: $ROOTVER"
[[ "$PYVER" =~ ^3\.[0-9]+\.[0-9]+$ ]] || die "Python version must look like 3.14.9: $PYVER"

NAME="V${ROOTVER}_${PYVER}"
PREFIX="$PRODUCTS/ROOT/$NAME"
PYPREFIX="$PRODUCTS/PYTHON/V$PYVER"
PY="$PYPREFIX/bin/python3"
RECIPE="$HERE/$NAME.yml"
OPTIONS="$HERE/$NAME.options"
WORKDIR="${WORKDIR:-${TMPDIR:-/tmp}/prod-build-root-$NAME}"
SRC="$WORKDIR/root-$ROOTVER"
BUILD="$WORKDIR/build"
URL="https://root.cern/download/root_v$ROOTVER.source.tar.gz"

[ -d "$PRODUCTS" ] || die "$PRODUCTS does not exist"
[ ! -e "$PRODUCTS/ROOT/$NAME.manifest" ] || die "ROOT $NAME is already released (it has a manifest); it must not change"
[ -x "$PY" ] || die "no $PY: build PYTHON V$PYVER first"
[ -f "$RECIPE" ] || die "no recipe $RECIPE"
[ -f "$OPTIONS" ] || die "no cmake options $OPTIONS"

umask 022
mkdir -p "$WORKDIR"
# the PYTHON is only read: no bytecode, no user site-packages
export PATH="$PYPREFIX/bin:/usr/bin:/bin" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1

options() { # the -D lines of the options file, without comments
    sed -e 's/#.*//' -e 's/[[:space:]]*$//' "$OPTIONS" | grep -v '^[[:space:]]*$'
}

configure() {
    local tarball="$WORKDIR/root_v$ROOTVER.source.tar.gz"
    if [ ! -f "$tarball" ]; then
        log "downloading $URL"
        curl -fL --retry 3 -o "$tarball.part" "$URL"
        mv "$tarball.part" "$tarball"
    fi
    log "source sha256 $(sha256sum "$tarball" | cut -d' ' -f1)"
    sha256sum "$tarball" | sed "s|$WORKDIR/||" > "$WORKDIR/source.sha256"
    if [ ! -d "$SRC" ]; then
        mkdir -p "$SRC.part"
        tar -C "$SRC.part" --strip-components=1 -xzf "$tarball"
        mv "$SRC.part" "$SRC"
    fi
    mkdir -p "$BUILD"
    local opts
    mapfile -t opts < <(options)
    log "cmake with $(basename "$OPTIONS") (log: $WORKDIR/configure.log)"
    # --fresh: no values left in the cache by an earlier run; only the options file counts
    cmake --fresh -S "$SRC" -B "$BUILD" -DCMAKE_INSTALL_PREFIX="$PREFIX" -DPython3_EXECUTABLE="$PY" "${opts[@]}" \
        > "$WORKDIR/configure.log" 2>&1 \
        || { grep -A8 'CMake Error' "$WORKDIR/configure.log" | head -40; die "cmake failed, see $WORKDIR/configure.log"; }
    grep -A3 -m1 'Enabled support for:' "$WORKDIR/configure.log" || true
    log "configured in $BUILD"
}

build() {
    [ -f "$BUILD/CMakeCache.txt" ] || die "not configured: run the 'configure' stage first"
    if [ -d "$PREFIX" ] && [ -n "$(ls -A "$PREFIX")" ]; then
        die "$PREFIX is not empty; remove it first to build again (chmod -R u+w if it was locked)"
    fi
    log "compiling with $JOBS jobs (an hour or more; log: $WORKDIR/build.log)"
    with_progress compiling "$WORKDIR/build.log" cmake --build "$BUILD" -j "$JOBS" \
        || { grep -n -m20 -i 'error' "$WORKDIR/build.log" || true; die "build failed, see $WORKDIR/build.log"; }
    log "installing into $PREFIX (log: $WORKDIR/install.log)"
    with_progress installing "$WORKDIR/install.log" cmake --install "$BUILD" || die "install failed, see $WORKDIR/install.log"
}

check() {
    local rc="$PREFIX/bin/root-config"
    [ -x "$rc" ] || die "no $rc: run the 'build' stage first"
    log "root-config: $("$rc" --version), python $("$rc" --python-version), $("$rc" --cflags | grep -o -- '-std=[^ ]*')"
    [ "$("$rc" --python-version)" = "$PYVER" ] || die "ROOT was built with Python $("$rc" --python-version), not $PYVER"
    log "features: $("$rc" --features)"
    log "running a macro and import ROOT (in a clean shell with thisroot.sh)"
    # a script file, not bash -c: thisroot.sh finds its shell and location through the process command line
    cat > "$WORKDIR/check-root.sh" <<'EOS'
set -e
. "$1/bin/thisroot.sh"
root -l -b -q -e 'TH1F h("h","h",10,-3,3); h.FillRandom("gaus",1000); if (h.GetEntries()!=1000) gSystem->Exit(1);'
python3 -B -c 'import ROOT, numpy; h = ROOT.TH1F("h", "h", 10, -3, 3); h.FillRandom("gaus", 100); print("import ROOT", ROOT.gROOT.GetVersion(), "entries", int(h.GetEntries()))'
EOS
    env -i HOME="$HOME" PATH="$PYPREFIX/bin:/usr/bin:/bin" LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
        bash --noprofile --norc "$WORKDIR/check-root.sh" "$PREFIX" || die "ROOT does not run"
    log "looking for missing shared libraries"
    local missing
    missing="$(missing_libs "$PREFIX")"
    if [ -n "$missing" ]; then
        echo "$missing" | head -20
        die "$(echo "$missing" | wc -l) missing shared libraries (above)"
    fi
    ready "$RECIPE" "$PREFIX" "$PRODUCTS"
}

case "$STAGE" in
    configure) configure ;;
    build) build; check ;;
    check) check ;;
    all) configure; build; check ;;
esac
