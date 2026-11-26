#!/bin/bash
# Build CUPDAQ <VERSION> at its final path <Products>/CUPDAQ/<VERSION> (staging, before
# `prodman release`).
#
#   build-recipes/CUPDAQ/build.sh <version> [configure|build|check|all] [--jobs N] [--workdir DIR] [--products_dir DIR]
#
#   <version> is given without the V: `build.sh 4.4` builds build-recipes/CUPDAQ/V4.4.yml into
#   <Products>/CUPDAQ/V4.4 (<VERSION> below is V<version>).
#
#   configure  check out the source tag of build-recipes/CUPDAQ/<VERSION>.yml (e.g. V4.4.yml), then run
#              cmake with its options
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
# - The source, the build tree and the logs go to --workdir (default /tmp/prod-build-cupdaq-<VERSION>), never
#   under Products.
#
# The binary is not relocatable: build at the final path, on the products server or on a node with the same OS
# (AlmaLinux 9) and the same Products path (then copy it to the server as printed at the end). At the end the
# recipe is saved next to the version, <Products>/CUPDAQ/<VERSION>.recipe.yml; `prodman release` reads it there.

set -euo pipefail

if [ -z "${_PROD_BUILD_CLEAN:-}" ]; then
    exec env -i HOME="$HOME" USER="${USER:-$(id -un)}" PATH=/usr/bin:/bin LANG=C.UTF-8 TERM="${TERM:-dumb}" \
        _PROD_BUILD_CLEAN=1 bash "$0" "$@"
fi

die() { echo "build.sh: $*" >&2; exit 1; }
log() { echo "build.sh: $*"; }

usage() { sed -n '2,/^[^#]/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/../_common/build-common.sh"

ARG=""
STAGE="all"
JOBS="$(nproc)"
WORKDIR=""
PRODUCTS="/home/cupsoft/Products"

while [ $# -gt 0 ]; do
    case "$1" in
        configure|build|check|all) STAGE="$1"; shift ;;
        --jobs) JOBS="${2:?}"; shift 2 ;;
        --workdir) WORKDIR="${2:?}"; shift 2 ;;
        --products_dir) PRODUCTS="${2:?}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        -*) usage >&2; die "unknown option: $1" ;;
        *) [ -z "$ARG" ] || die "only one version"; ARG="$1"; shift ;;
    esac
done

[ -n "$ARG" ] || { usage >&2; die "give the version without the V, e.g. 4.4"; }
[[ "$ARG" =~ ^[0-9][A-Za-z0-9._-]*$ ]] || die "the version must look like 4.4 (without the V): $ARG"
NAME="V$ARG"

PREFIX="$PRODUCTS/CUPDAQ/$NAME"
RECIPE="$HERE/$NAME.yml"
WORKDIR="${WORKDIR:-${TMPDIR:-/tmp}/prod-build-cupdaq-$NAME}"
case "$WORKDIR" in /*) ;; *) WORKDIR="$PWD/$WORKDIR" ;; esac  # absolute: the checks run in other directories
SRC="$WORKDIR/src"
BUILD="$WORKDIR/build"

[ -d "$PRODUCTS" ] || die "$PRODUCTS does not exist"
[ ! -e "$PRODUCTS/CUPDAQ/$NAME.manifest" ] || die "CUPDAQ $NAME is already released (it has a manifest); it must not change"
[ -f "$RECIPE" ] || die "no recipe $RECIPE"

umask 022
mkdir -p "$WORKDIR"
read_recipe "$RECIPE" # sets SRC_URL, SRC_TAG, OPTIONS
load_depends "$RECIPE" "$PRODUCTS" "$WORKDIR" # sets DEPS, DEP_LIBDIRS

configure() {
    fetch_source "$SRC"
    log "cmake with the recipe's options (log: $WORKDIR/configure.log)"
    # --fresh: no values left in the cache by an earlier run; only the recipe's options count
    cmake --fresh -S "$SRC" -B "$BUILD" -DCMAKE_INSTALL_PREFIX="$PREFIX" "${OPTIONS[@]}" \
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
    # send_daq_command.py is in the V4.3.1 source (28ca0dc) but not in the v4.3 and v4.4 tags
    if [ -f "$PREFIX/bin/send_daq_command.py" ]; then
        (cd "$ex" && python3 "$PREFIX/bin/send_daq_command.py" --help) >> "$WORKDIR/check.log" 2>&1 \
            || { tail -5 "$WORKDIR/check.log"; die "send_daq_command.py --help failed (python3: $(command -v python3))"; }
        echo "  send_daq_command.py: OK with $(command -v python3)"
    else
        echo "  send_daq_command.py: not in this version"
    fi
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
