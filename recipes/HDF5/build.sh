#!/bin/bash
# Build HDF5 <version> at its final path <Products>/HDF5/V<version> (staging, before `prod release`).
#
#   recipes/HDF5/build.sh <version> [configure|build|check|all] [--jobs N] [--workdir DIR] [--products DIR]
#
#   configure  download the source and run cmake with recipes/HDF5/V<version>.options
#   build      compile, run the library's own tests (ctest), install, then 'check'
#   check      libhdf5.settings, and recipes/HDF5/check (found with find_package like the DAQ does) built
#              against the installation: writes and reads back a chunked, deflated compound dataset; h5dump
#   all        configure, then build (default)
#
# - Runs in a clean environment (no variables from a loaded vlist).
# - Refuses a version that is already frozen (it has a manifest), and a non-empty prefix for 'build'.
# - The source, the build tree, the check and the logs go to --workdir (default /tmp/prod-build-hdf5-<version>),
#   never under Products.
#
# The binary is not relocatable: build at the final path, on the products server or on a node with the same OS
# (AlmaLinux 9) and the same Products path (then copy it to the server as printed at the end). At the end the
# recipe is saved next to the version, <Products>/HDF5/V<version>.recipe.yml; `prod release` reads it there.

set -euo pipefail

if [ -z "${_PROD_BUILD_CLEAN:-}" ]; then
    exec env -i HOME="$HOME" USER="${USER:-$(id -un)}" PATH=/usr/bin:/bin LANG=C.UTF-8 TERM="${TERM:-dumb}" \
        _PROD_BUILD_CLEAN=1 bash "$0" "$@"
fi

die() { echo "build.sh: $*" >&2; exit 1; }
log() { echo "build.sh: $*"; }

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/../_common/build-common.sh" # with_progress, missing_libs

progress_hint() { # ctest's last "12/700 Test", else cmake's last percentage
    local t
    t="$(grep -oE '^ *[0-9]+/[0-9]+ Test' "$1" 2>/dev/null | tail -1)"
    if [ -n "$t" ]; then
        echo "test $(echo "$t" | tr -dc '0-9/')"
    else
        grep -o '^\[ *[0-9]*%\]' "$1" 2>/dev/null | tail -1 | tr -d '[] '
    fi
}

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

[ -n "$VER" ] || { usage >&2; die "give the HDF5 version, e.g. 2.2.0"; }
[[ "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like 2.2.0: $VER"

NAME="V$VER"
PREFIX="$PRODUCTS/HDF5/$NAME"
RECIPE="$HERE/$NAME.yml"
OPTIONS="$HERE/$NAME.options"
WORKDIR="${WORKDIR:-${TMPDIR:-/tmp}/prod-build-hdf5-$VER}"
SRC="$WORKDIR/hdf5-$VER"
BUILD="$WORKDIR/build"
URL="https://github.com/HDFGroup/hdf5/releases/download/$VER/hdf5-$VER.tar.gz"

[ -d "$PRODUCTS" ] || die "$PRODUCTS does not exist"
[ ! -e "$PRODUCTS/HDF5/$NAME.manifest" ] || die "HDF5 $NAME is already released (it has a manifest); it must not change"
[ -f "$RECIPE" ] || die "no recipe $RECIPE"
[ -f "$OPTIONS" ] || die "no cmake options $OPTIONS"

umask 022
mkdir -p "$WORKDIR"

options() { # the -D lines of the options file, without comments
    sed -e 's/#.*//' -e 's/[[:space:]]*$//' "$OPTIONS" | grep -v '^[[:space:]]*$'
}

configure() {
    local tarball="$WORKDIR/hdf5-$VER.tar.gz"
    if [ ! -f "$tarball" ]; then
        log "downloading $URL"
        curl -fL --retry 3 -o "$tarball.part" "$URL"
        mv "$tarball.part" "$tarball"
    fi
    log "source sha256 $(sha256sum "$tarball" | cut -d' ' -f1)"
    sha256sum "$tarball" | sed "s|$WORKDIR/||" > "$WORKDIR/source.sha256"
    if [ ! -d "$SRC" ]; then
        # the 2.x tarballs hold ./hdf5-<version>/...: take the shallowest directory with a CMakeLists.txt
        rm -rf "$SRC.part"
        mkdir -p "$SRC.part"
        tar -C "$SRC.part" -xzf "$tarball"
        local top
        top="$(find "$SRC.part" -maxdepth 3 -name CMakeLists.txt -printf '%d %h\n' | sort -n | head -1 | cut -d' ' -f2-)"
        [ -n "$top" ] || die "no CMakeLists.txt in $tarball"
        mv "$top" "$SRC"
        rm -rf "$SRC.part"
    fi
    mkdir -p "$BUILD"
    local opts
    mapfile -t opts < <(options)
    log "cmake with $(basename "$OPTIONS") (log: $WORKDIR/configure.log)"
    # --fresh: no values left in the cache by an earlier run; only the options file counts
    cmake --fresh -S "$SRC" -B "$BUILD" -DCMAKE_INSTALL_PREFIX="$PREFIX" "${opts[@]}" \
        > "$WORKDIR/configure.log" 2>&1 \
        || { grep -A8 'CMake Error' "$WORKDIR/configure.log" | head -40; die "cmake failed, see $WORKDIR/configure.log"; }
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
    log "running the library's tests with $JOBS jobs (log: $WORKDIR/test.log)"
    with_progress testing "$WORKDIR/test.log" ctest --test-dir "$BUILD" -j "$JOBS" --timeout 1200 \
        || { grep -A30 'tests failed out of' "$WORKDIR/test.log" | head -40 || true; die "tests failed, see $WORKDIR/test.log"; }
    grep -m1 'tests passed' "$WORKDIR/test.log" || true
    log "installing into $PREFIX (log: $WORKDIR/install.log)"
    with_progress installing "$WORKDIR/install.log" cmake --install "$BUILD" || die "install failed, see $WORKDIR/install.log"
}

check() {
    local settings="$PREFIX/lib/libhdf5.settings"
    [ -f "$settings" ] || die "no $settings: run the 'build' stage first"
    setting() { sed -n "s|^[[:space:]]*$1:[[:space:]]*||p" "$settings" | head -1; } # keys hold '/' and '+'
    log "libhdf5.settings: version $(setting 'HDF5 Version'), C++ $(setting 'C++'), high-level $(setting 'High-level library')," \
        "filters $(setting 'I/O filters (external)'), API $(setting 'Default API mapping')"
    [ "$(setting 'HDF5 Version')" = "$VER" ] || die "libhdf5.settings says HDF5 $(setting 'HDF5 Version'), not $VER"
    log "building and running recipes/HDF5/check against the installation (log: $WORKDIR/check.log)"
    local ex="$WORKDIR/check"
    rm -rf "$ex"
    mkdir -p "$ex"
    # the variables of the HDF5 modulefile, nothing else
    env -i HOME="$HOME" LANG=C.UTF-8 HDF5SYS="$PREFIX" PATH="$PREFIX/bin:/usr/bin:/bin" \
        LD_LIBRARY_PATH="$PREFIX/lib" CMAKE_PREFIX_PATH="$PREFIX" bash --noprofile --norc -c '
        set -e
        cmake -S "$1" -B "$2/build"
        cmake --build "$2/build"
        "$2/build/h5check" "$2/h5check.h5"
        h5dump -H "$2/h5check.h5"
        ldd "$2/build/h5check" | grep hdf5' _ "$HERE/check" "$ex" > "$WORKDIR/check.log" 2>&1 \
        || { tail -30 "$WORKDIR/check.log"; die "the check program failed"; }
    grep -E 'h5check: (HDF5 |libraries)' "$WORKDIR/check.log" | sed 's/^-- //'
    # the DAQ must get this HDF5, not another one on the system
    grep -q "h5check: HDF5 $VER, include $PREFIX/include" "$WORKDIR/check.log" \
        || die "find_package found another HDF5 (see $WORKDIR/check.log)"
    if grep 'hdf5' "$WORKDIR/check.log" | grep '=>' | grep -v -- "=> $PREFIX/lib/" ; then
        die "h5check runs with an HDF5 library outside $PREFIX (above)"
    fi
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
