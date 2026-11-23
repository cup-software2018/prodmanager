#!/bin/bash
# Build CUPDAQ <version> from GitHub at its final path <Products>/CUPDAQ/V<version> (staging, before
# `prod release`).
#
#   recipes/CUPDAQ/build.sh <version> [configure|build|check|all] [--jobs N] [--workdir DIR] [--products DIR]
#
#   configure  clone the source and check out the commit in recipes/CUPDAQ/V<version>.yml, then run cmake with
#              recipes/CUPDAQ/V<version>.options
#   build      compile and install, then 'check'
#   check      RPATH; every program starts (no arguments: prints its usage); send_daq_command.py runs with the
#              products' Python; ROOT reads the sample configuration DAQ/config/all.yml with RunConfig and finds
#              every class by name (rootmap, dictionary); missing shared libraries. No hardware is used.
#   all        configure, then build (default)
#
# - The build loads the modulefiles of the recipe's depends (ROOT, HDF5, CUPDATAMODEL) and nothing else. A
#   dependency that is not released yet (e.g. a CUPDATAMODEL built just before) gets a modulefile made from the
#   repository's template; release it in the same vlist. libusb, yaml-cpp and zeromq come from the system.
# - Refuses a version that is already frozen (it has a manifest), and a non-empty prefix for 'build'.
# - The source, the build tree and the logs go to --workdir (default /tmp/prod-build-cupdaq-<version>), never
#   under Products.
#
# The binary is not relocatable: build at the final path, on the products server or on a node with the same OS
# (AlmaLinux 9) and the same Products path (then copy it to the server as printed at the end). At the end the
# recipe is saved next to the version, <Products>/CUPDAQ/V<version>.recipe.yml; `prod release` reads it there.

set -euo pipefail

if [ -z "${_PROD_BUILD_CLEAN:-}" ]; then
    exec env -i HOME="$HOME" USER="${USER:-$(id -un)}" PATH=/usr/bin:/bin LANG=C.UTF-8 TERM="${TERM:-dumb}" \
        _PROD_BUILD_CLEAN=1 bash "$0" "$@"
fi

die() { echo "build.sh: $*" >&2; exit 1; }
log() { echo "build.sh: $*"; }

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/../_common/build-common.sh"

VER=""
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
        *) [ -z "$VER" ] || die "only one version"; VER="$1"; shift ;;
    esac
done

[ -n "$VER" ] || { usage >&2; die "give the CUPDAQ version, e.g. 4.3.1"; }
[[ "$VER" =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "version must look like 4.3.1: $VER"

NAME="V$VER"
PREFIX="$PRODUCTS/CUPDAQ/$NAME"
RECIPE="$HERE/$NAME.yml"
OPTIONS="$HERE/$NAME.options"
WORKDIR="${WORKDIR:-${TMPDIR:-/tmp}/prod-build-cupdaq-$VER}"
SRC="$WORKDIR/src"
BUILD="$WORKDIR/build"

[ -d "$PRODUCTS" ] || die "$PRODUCTS does not exist"
[ ! -e "$PRODUCTS/CUPDAQ/$NAME.manifest" ] || die "CUPDAQ $NAME is already released (it has a manifest); it must not change"
[ -f "$RECIPE" ] || die "no recipe $RECIPE"
[ -f "$OPTIONS" ] || die "no cmake options $OPTIONS"

umask 022
mkdir -p "$WORKDIR"
load_depends "$RECIPE" "$PRODUCTS" "$WORKDIR" # sets SRC_URL, SRC_COMMIT, DEPS, DEP_LIBDIRS

options() { # the -D lines of the options file, without comments
    sed -e 's/#.*//' -e 's/[[:space:]]*$//' "$OPTIONS" | grep -v '^[[:space:]]*$'
}

configure() {
    fetch_git "$SRC_URL" "$SRC_COMMIT" "$SRC"
    local opts
    mapfile -t opts < <(options)
    log "cmake with $(basename "$OPTIONS") (log: $WORKDIR/configure.log)"
    # --fresh: no values left in the cache by an earlier run; only the options file counts
    cmake --fresh -S "$SRC" -B "$BUILD" -DCMAKE_INSTALL_PREFIX="$PREFIX" "${opts[@]}" \
        > "$WORKDIR/configure.log" 2>&1 \
        || { grep -A8 'CMake Error' "$WORKDIR/configure.log" | head -40; die "cmake failed, see $WORKDIR/configure.log"; }
    grep -E 'Found (ROOT|HDF5|libusb|ZeroMQ)|Found libusb' "$WORKDIR/configure.log" | sed 's/^-- */  /'
    # the products' ROOT, CUPDataModel and HDF5, not others on the system
    grep -q "Found ROOT .*: $ROOTSYS/" "$WORKDIR/configure.log" || die "cmake did not find ROOT in $ROOTSYS"
    grep -q "^CUPDataModel_DIR:PATH=$CUPDM_DIR/" "$BUILD/CMakeCache.txt" || die "cmake did not find CUPDataModel in $CUPDM_DIR"
    grep -q "^CUPDAQ_ENABLE_HDF5:BOOL=ON" "$BUILD/CMakeCache.txt" || die "CUPDataModel has no HDF5Utils: CUPDAQ would be built without HDF5"
    log "configured in $BUILD"
}

build() {
    [ -f "$BUILD/CMakeCache.txt" ] || die "not configured: run the 'configure' stage first"
    if [ -d "$PREFIX" ] && [ -n "$(ls -A "$PREFIX")" ]; then
        die "$PREFIX is not empty; remove it first to build again (chmod -R u+w if it was locked)"
    fi
    log "compiling with $JOBS jobs (log: $WORKDIR/build.log)"
    with_progress compiling "$WORKDIR/build.log" cmake --build "$BUILD" -j "$JOBS" \
        || { grep -n -m20 -i 'error' "$WORKDIR/build.log" || true; die "build failed, see $WORKDIR/build.log"; }
    log "$(grep -c 'warning:' "$WORKDIR/build.log" || true) compiler warnings"
    log "installing into $PREFIX (log: $WORKDIR/install.log)"
    with_progress installing "$WORKDIR/install.log" cmake --install "$BUILD" || die "install failed, see $WORKDIR/install.log"
    # CUPDAQ links with ${ROOT_USE_FILE}, whose link directory is ROOT's with the Products symlink resolved
    unresolve_rpath "$PREFIX"
}

check() {
    [ -x "$PREFIX/bin/daq" ] || die "no $PREFIX/bin/daq: run the 'build' stage first"
    local config="$SRC/DAQ/config/all.yml"
    [ -f "$config" ] || die "no $config: run the 'configure' stage first"
    check_rpath "$PREFIX"

    # the variables of modulefiles/CUPDAQ/template.tcl
    export CUPDAQ_DIR="$PREFIX"
    export PATH="$PREFIX/bin:$PATH"
    export LD_LIBRARY_PATH="$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
    export ROOT_INCLUDE_PATH="$PREFIX/include:${ROOT_INCLUDE_PATH:-}"

    local ex="$WORKDIR/check" p rc
    rm -rf "$ex"
    mkdir -p "$ex"
    log "starting every program without arguments (log: $WORKDIR/check.log)"
    : > "$WORKDIR/check.log"
    for p in "$PREFIX"/bin/*; do
        head -c4 "$p" | grep -q ELF || continue
        rc=0
        (cd "$ex" && timeout 60 "$p" < /dev/null) >> "$WORKDIR/check.log" 2>&1 || rc=$?
        [ "$rc" = 0 ] || { tail -5 "$WORKDIR/check.log"; die "$(basename "$p") without arguments: exit code $rc"; }
        echo "  $(basename "$p"): OK"
    done
    (cd "$ex" && python3 "$PREFIX/bin/send_daq_command.py" --help) >> "$WORKDIR/check.log" 2>&1 \
        || { tail -5 "$WORKDIR/check.log"; die "send_daq_command.py --help failed (python3: $(command -v python3))"; }
    echo "  send_daq_command.py: OK with $(command -v python3)"
    if grep -E 'error while loading shared libraries' "$WORKDIR/check.log"; then
        die "a program cannot find its libraries (above)"
    fi

    log "reading DAQ/config/all.yml with RunConfig in ROOT (log: $WORKDIR/configcheck.log)"
    (cd "$ex" && root -l -b -q -n "$HERE/check/configcheck.C(\"$config\")") > "$WORKDIR/configcheck.log" 2>&1 \
        || { tail -20 "$WORKDIR/configcheck.log"; die "root failed"; }
    grep '^configcheck:' "$WORKDIR/configcheck.log" | sed 's/^/  /'
    grep -q '^configcheck: OK' "$WORKDIR/configcheck.log" || die "RunConfig cannot read $config (see $WORKDIR/configcheck.log)"
    if grep -E 'Error in <|Warning in <' "$WORKDIR/configcheck.log"; then
        die "ROOT reported errors or warnings (above)"
    fi
    log "loading every class in ROOT (log: $WORKDIR/rootcheck.log)"
    (cd "$ex" && check_root_classes "$PREFIX/lib64" "$WORKDIR/rootcheck.log")

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
