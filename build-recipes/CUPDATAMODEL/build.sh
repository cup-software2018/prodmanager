#!/bin/bash
# Build CUPDATAMODEL <VERSION> at its final path <Products>/CUPDATAMODEL/<VERSION> (staging, before
# `prodman release`).
#
#   build-recipes/CUPDATAMODEL/build.sh <version> [configure|build|check|all] [--jobs N] [--workdir DIR] [--products_dir DIR]
#
#   <version> is given without the V: `build.sh 1.4.1` builds build-recipes/CUPDATAMODEL/V1.4.1.yml into
#   <Products>/CUPDATAMODEL/V1.4.1 (<VERSION> below is V<version>).
#
#   configure  check out the source tag of build-recipes/CUPDATAMODEL/<VERSION>.yml (e.g. V1.4.1.yml), then run
#              cmake with its options
#   build      compile and install, then 'check'
#   check      RPATH of the libraries; build-recipes/CUPDATAMODEL/check, found with find_package(CUPDataModel) like
#              CUPDAQ does: writes and reads back SADC and FADC events (HDF5Utils) and a TTree (RawObjs); ROOT
#              finds every class by name (rootmap, dictionary); missing shared libraries
#   all        configure, then build (default)
#
# - The build loads the modulefiles of the recipe's depends (ROOT, HDF5) and nothing else, so it sees exactly
#   what a user of the vlist gets (no loaded vlist, no system HDF5). A dependency that is not released yet gets
#   a modulefile made from the repository's template; release it in the same vlist.
# - Refuses a version that is already frozen (it has a manifest), and a non-empty prefix for 'build'.
# - The source, the build tree and the logs go to --workdir (default /tmp/prod-build-cupdatamodel-<VERSION>),
#   never under Products.
#
# The binary is not relocatable: build at the final path, on the products server or on a node with the same OS
# (AlmaLinux 9) and the same Products path (then copy it to the server as printed at the end). At the end the
# recipe is saved next to the version, <Products>/CUPDATAMODEL/<VERSION>.recipe.yml; `prodman release` reads it there.

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

[ -n "$ARG" ] || { usage >&2; die "give the version without the V, e.g. 1.4.1"; }
[[ "$ARG" =~ ^[0-9][A-Za-z0-9._-]*$ ]] || die "the version must look like 1.4.1 (without the V): $ARG"
NAME="V$ARG"

PREFIX="$PRODUCTS/CUPDATAMODEL/$NAME"
RECIPE="$HERE/$NAME.yml"
WORKDIR="${WORKDIR:-${TMPDIR:-/tmp}/prod-build-cupdatamodel-$NAME}"
case "$WORKDIR" in /*) ;; *) WORKDIR="$PWD/$WORKDIR" ;; esac  # absolute: the checks run in other directories
SRC="$WORKDIR/src"
BUILD="$WORKDIR/build"

[ -d "$PRODUCTS" ] || die "$PRODUCTS does not exist"
[ ! -e "$PRODUCTS/CUPDATAMODEL/$NAME.manifest" ] || die "CUPDATAMODEL $NAME is already released (it has a manifest); it must not change"
[ -f "$RECIPE" ] || die "no recipe $RECIPE"

umask 022
mkdir -p "$WORKDIR"
read_recipe "$RECIPE" # sets SRC_URL, SRC_TAG, OPTIONS
load_depends "$RECIPE" "$PRODUCTS" "$WORKDIR" # sets DEPS, DEP_LIBDIRS

dep_version() { # <NAME>: the upstream version of that dependency (ROOT/V6.40.04_3.14.9 -> 6.40.04)
    local d v
    for d in "${DEPS[@]}"; do
        if [ "${d%%/*}" = "$1" ]; then v="${d#*/V}"; echo "${v%%_*}"; return; fi
    done
}

configure() {
    fetch_source "$SRC"
    log "cmake with the recipe's options (log: $WORKDIR/configure.log)"
    # --fresh: no values left in the cache by an earlier run; only the recipe's options count
    cmake --fresh -S "$SRC" -B "$BUILD" -DCMAKE_INSTALL_PREFIX="$PREFIX" "${OPTIONS[@]}" \
        > "$WORKDIR/configure.log" 2>&1 \
        || { grep -A8 'CMake Error' "$WORKDIR/configure.log" | head -40; die "cmake failed, see $WORKDIR/configure.log"; }
    grep -E 'Found ROOT|HDF5 found|HDF5 not found' "$WORKDIR/configure.log" | sed 's/^-- /  /'
    # the products' ROOT and HDF5, not others on the system; HDF5Utils must be built
    local rv hv
    rv="$(dep_version ROOT)"
    hv="$(dep_version HDF5)"
    grep -q "Found ROOT $rv: $ROOTSYS/" "$WORKDIR/configure.log" || die "cmake did not find ROOT $rv in $ROOTSYS"
    grep -q "HDF5 found ($hv)" "$WORKDIR/configure.log" || die "cmake did not find HDF5 $hv (HDF5Utils needs it)"
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
    unresolve_rpath "$PREFIX"
}

check() {
    [ -f "$PREFIX/lib64/cmake/CUPDataModel/CUPDataModelConfig.cmake" ] || die "no CMake package in $PREFIX: run the 'build' stage first"
    check_rpath "$PREFIX"

    log "building and running build-recipes/CUPDATAMODEL/check against the installation (log: $WORKDIR/check.log)"
    local ex="$WORKDIR/check"
    rm -rf "$ex"
    mkdir -p "$ex"
    # the variables of modulefiles/CUPDATAMODEL/template.tcl
    export CUPDM_DIR="$PREFIX"
    export LD_LIBRARY_PATH="$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
    export ROOT_INCLUDE_PATH="$PREFIX/include:${ROOT_INCLUDE_PATH:-}"
    export CMAKE_PREFIX_PATH="$PREFIX:${CMAKE_PREFIX_PATH:-}"
    (
        set -x
        cmake -S "$HERE/check" -B "$ex/build"
        cmake --build "$ex/build"
        "$ex/build/cupdmcheck" "$ex"
        ldd "$ex/build/cupdmcheck"
    ) > "$WORKDIR/check.log" 2>&1 || { tail -30 "$WORKDIR/check.log"; die "the check program failed"; }
    grep -E '^(-- )?cupdmcheck:' "$WORKDIR/check.log" | sed 's/^-- //'
    grep -q "cupdmcheck: CUPDataModel .* from $PREFIX/" "$WORKDIR/check.log" \
        || die "find_package found another CUPDataModel (see $WORKDIR/check.log)"
    # the check program must run with this installation and the dependencies' libraries
    if grep -E 'lib(RawObjs|HDF5Utils|hdf5[^ ]*|Core)\.so' "$WORKDIR/check.log" | grep '=>' \
            | grep -v -e "=> $PREFIX/lib64/" -e "=> $ROOTSYS/lib/" -e "=> $HDF5SYS/lib/"; then
        die "the check program runs with libraries from elsewhere (above)"
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
