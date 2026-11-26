#!/bin/bash
# Build Geant4 <VERSION> at its final path <Products>/GEANT4/<VERSION> (staging, before `prodman release`).
#
#   build-recipes/GEANT4/build.sh <version> [configure|build|check|all] [--jobs N] [--workdir DIR] [--products_dir DIR]
#
#   <version> is given without the V: `build.sh 11.4.3` builds build-recipes/GEANT4/V11.4.3.yml into
#   <Products>/GEANT4/V11.4.3 (<VERSION> below is V<version>).
#
#   configure  download the source of build-recipes/GEANT4/<VERSION>.yml (e.g. V11.4.3.yml) and run
#              cmake with its options; prints the features
#   build      compile and install (cmake --build, then --install; the data sets are downloaded), then 'check'
#   check      geant4-config, geant4make.sh, and example B1 built against the installation and run in batch
#   all        configure, then build (default)
#
# - Runs in a clean environment (no variables from a loaded vlist).
# - Refuses a version that is already frozen (it has a manifest), and a non-empty prefix for 'build'.
# - The source, the build tree, the example check and the logs go to --workdir (default
#   /tmp/prod-build-geant4-<VERSION>), never under Products.
#
# The binary is not relocatable: build at the final path, on the products server or on a node with the same OS
# (AlmaLinux 9) and the same Products path (then copy it to the server as printed at the end). At the end the
# recipe is saved next to the version, <Products>/GEANT4/<VERSION>.recipe.yml; `prodman release` reads it there.

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

[ -n "$ARG" ] || { usage >&2; die "give the version without the V, e.g. 11.4.3"; }
[[ "$ARG" =~ ^[0-9][A-Za-z0-9._-]*$ ]] || die "the version must look like 11.4.3 (without the V): $ARG"
NAME="V$ARG"

PREFIX="$PRODUCTS/GEANT4/$NAME"
RECIPE="$HERE/$NAME.yml"
WORKDIR="${WORKDIR:-${TMPDIR:-/tmp}/prod-build-geant4-$NAME}"
case "$WORKDIR" in /*) ;; *) WORKDIR="$PWD/$WORKDIR" ;; esac  # absolute: the checks run in other directories
SRC="$WORKDIR/src"
BUILD="$WORKDIR/build"

[ -d "$PRODUCTS" ] || die "$PRODUCTS does not exist"
[ ! -e "$PRODUCTS/GEANT4/$NAME.manifest" ] || die "GEANT4 $NAME is already released (it has a manifest); it must not change"
[ -f "$RECIPE" ] || die "no recipe $RECIPE"

umask 022
mkdir -p "$WORKDIR"
read_recipe "$RECIPE" # sets SRC_URL, SRC_TAG, OPTIONS

configure() {
    fetch_source "$SRC"
    mkdir -p "$BUILD"
    log "cmake with the recipe's options (log: $WORKDIR/configure.log)"
    # --fresh: no values left in the cache by an earlier run; only the recipe's options count
    cmake --fresh -S "$SRC" -B "$BUILD" -DCMAKE_INSTALL_PREFIX="$PREFIX" "${OPTIONS[@]}" \
        > "$WORKDIR/configure.log" 2>&1 \
        || { grep -A8 'CMake Error' "$WORKDIR/configure.log" | head -40; die "cmake failed, see $WORKDIR/configure.log"; }
    sed -n '/The following Geant4 features are enabled/,/^$/p' "$WORKDIR/configure.log"
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
    log "installing into $PREFIX, with the data sets (log: $WORKDIR/install.log)"
    with_progress installing "$WORKDIR/install.log" cmake --install "$BUILD" || die "install failed, see $WORKDIR/install.log"
}

check() {
    local gc="$PREFIX/bin/geant4-config"
    [ -x "$gc" ] || die "no $gc: run the 'build' stage first"
    log "geant4-config: $("$gc" --version); features: $("$gc" --features | tr -s ' \n' ' ')"
    if ! "$gc" --check-datasets > "$WORKDIR/check-datasets.log" 2>&1 || grep -q -i 'notfound' "$WORKDIR/check-datasets.log"; then
        cat "$WORKDIR/check-datasets.log"
        die "data sets missing"
    fi
    ls "$PREFIX"/share/Geant4*/geant4make/geant4make.sh >/dev/null 2>&1 \
        || die "no share/Geant4*/geant4make/geant4make.sh (the modulefile is generated from it)"
    log "building and running example B1 against the installation (log: $WORKDIR/check-B1.log)"
    local ex="$WORKDIR/check-B1"
    rm -rf "$ex"
    mkdir -p "$ex"
    cat > "$WORKDIR/check-B1.sh" <<'EOS'
set -e
. "$1/bin/geant4.sh"
cmake -S "$1"/share/Geant4/examples/basic/B1 -B "$2/build" -DGeant4_DIR="$(ls -d "$1"/lib64/cmake/Geant4* | head -1)"
cmake --build "$2/build" -j 4
cd "$2/build"
printf '/run/numberOfThreads 2\n/run/initialize\n/run/beamOn 100\n' > check.mac
./exampleB1 check.mac
EOS
    env -i HOME="$HOME" PATH=/usr/bin:/bin LANG=C.UTF-8 bash --noprofile --norc "$WORKDIR/check-B1.sh" "$PREFIX" "$ex" \
        > "$WORKDIR/check-B1.log" 2>&1 || { tail -30 "$WORKDIR/check-B1.log"; die "example B1 failed"; }
    # the master's summary: the first "Absorbed dose" after "End of Global Run"
    sed -n '/End of Global Run/,$p' "$WORKDIR/check-B1.log" | grep -m1 'Absorbed dose' || true
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
