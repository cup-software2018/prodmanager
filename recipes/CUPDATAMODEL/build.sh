#!/bin/bash
# Build CUPDATAMODEL <version> from GitHub at its final path <Products>/CUPDATAMODEL/V<version> (staging, before
# `prod release`).
#
#   recipes/CUPDATAMODEL/build.sh <version> [configure|build|check|all] [--jobs N] [--workdir DIR] [--products DIR]
#
#   configure  clone the source and check out the commit in recipes/CUPDATAMODEL/V<version>.yml, then run cmake
#              with recipes/CUPDATAMODEL/V<version>.options
#   build      compile and install, then 'check'
#   check      RPATH of the libraries; recipes/CUPDATAMODEL/check, found with find_package(CUPDataModel) like
#              CUPDAQ does: writes and reads back SADC and FADC events (HDF5Utils) and a TTree (RawObjs); ROOT
#              finds every class by name (rootmap, dictionary); missing shared libraries
#   all        configure, then build (default)
#
# - The build loads the modulefiles of the recipe's depends (ROOT, HDF5) and nothing else, so it sees exactly
#   what a user of the vlist gets (no loaded vlist, no system HDF5). A dependency that is not released yet gets
#   a modulefile made from the repository's template; release it in the same vlist.
# - Refuses a version that is already frozen (it has a manifest), and a non-empty prefix for 'build'.
# - The source, the build tree and the logs go to --workdir (default /tmp/prod-build-cupdatamodel-<version>),
#   never under Products.
#
# The binary is not relocatable: build at the final path, on the products server or on a node with the same OS
# (AlmaLinux 9) and the same Products path (then copy it to the server as printed at the end). At the end the
# recipe is saved next to the version, <Products>/CUPDATAMODEL/V<version>.recipe.yml; `prod release` reads it there.

set -euo pipefail

if [ -z "${_PROD_BUILD_CLEAN:-}" ]; then
    exec env -i HOME="$HOME" USER="${USER:-$(id -un)}" PATH=/usr/bin:/bin LANG=C.UTF-8 TERM="${TERM:-dumb}" \
        _PROD_BUILD_CLEAN=1 bash "$0" "$@"
fi

die() { echo "build.sh: $*" >&2; exit 1; }
log() { echo "build.sh: $*"; }

usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; }

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

[ -n "$VER" ] || { usage >&2; die "give the CUPDATAMODEL version, e.g. 1.3"; }
[[ "$VER" =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "version must look like 1.3: $VER"

NAME="V$VER"
PREFIX="$PRODUCTS/CUPDATAMODEL/$NAME"
RECIPE="$HERE/$NAME.yml"
OPTIONS="$HERE/$NAME.options"
WORKDIR="${WORKDIR:-${TMPDIR:-/tmp}/prod-build-cupdatamodel-$VER}"
SRC="$WORKDIR/src"
BUILD="$WORKDIR/build"

[ -d "$PRODUCTS" ] || die "$PRODUCTS does not exist"
[ ! -e "$PRODUCTS/CUPDATAMODEL/$NAME.manifest" ] || die "CUPDATAMODEL $NAME is already released (it has a manifest); it must not change"
[ -f "$RECIPE" ] || die "no recipe $RECIPE"
[ -f "$OPTIONS" ] || die "no cmake options $OPTIONS"

umask 022
mkdir -p "$WORKDIR"
load_depends "$RECIPE" "$PRODUCTS" "$WORKDIR" # sets SRC_URL, SRC_COMMIT, DEPS, DEP_LIBDIRS

dep_version() { # <NAME>: the upstream version of that dependency (ROOT/V6.40.04_3.14.9 -> 6.40.04)
    local d v
    for d in "${DEPS[@]}"; do
        if [ "${d%%/*}" = "$1" ]; then v="${d#*/V}"; echo "${v%%_*}"; return; fi
    done
}

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

    log "building and running recipes/CUPDATAMODEL/check against the installation (log: $WORKDIR/check.log)"
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
