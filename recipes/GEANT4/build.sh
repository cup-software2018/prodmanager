#!/bin/bash
# Build Geant4 <version> at its final path <Products>/GEANT4/V<version> (staging, before `prod release`).
#
#   recipes/GEANT4/build.sh <version> [configure|build|check|all] [--jobs N] [--workdir DIR] [--products DIR]
#
#   configure  download the source and run cmake with recipes/GEANT4/V<version>.options; prints the features
#   build      compile and install (cmake --build, then --install; the data sets are downloaded), then 'check'
#   check      geant4-config, geant4make.sh, and example B1 built against the installation and run in batch
#   all        configure, then build (default)
#
# - Runs in a clean environment (no variables from a loaded vlist).
# - Refuses a version that is already frozen (it has a manifest), and a non-empty prefix for 'build'.
# - The source, the build tree, the example check and the logs go to --workdir (default
#   /tmp/prod-build-geant4-<version>), never under Products.
#
# The binary is not relocatable: build on a node with the same OS as the others (AlmaLinux 9) and the same
# Products path, then copy the prefix to the server with `rsync -a` before `prod release`.

set -euo pipefail

if [ -z "${_PROD_BUILD_CLEAN:-}" ]; then
    exec env -i HOME="$HOME" USER="${USER:-$(id -un)}" PATH=/usr/bin:/bin LANG=C.UTF-8 TERM="${TERM:-dumb}" \
        _PROD_BUILD_CLEAN=1 bash "$0" "$@"
fi

die() { echo "build.sh: $*" >&2; exit 1; }
log() { echo "build.sh: $*"; }

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/../_common/build-common.sh" # with_progress, missing_libs
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

[ -n "$VER" ] || { usage >&2; die "give the Geant4 version, e.g. 11.4.3"; }
[[ "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like 11.4.3: $VER"

NAME="V$VER"
PREFIX="$PRODUCTS/GEANT4/$NAME"
OPTIONS="$HERE/$NAME.options"
WORKDIR="${WORKDIR:-${TMPDIR:-/tmp}/prod-build-geant4-$VER}"
SRC="$WORKDIR/geant4-v$VER"
BUILD="$WORKDIR/build"
URL="https://gitlab.cern.ch/geant4/geant4/-/archive/v$VER/geant4-v$VER.tar.gz"

[ -d "$PRODUCTS" ] || die "$PRODUCTS does not exist"
[ ! -e "$PRODUCTS/GEANT4/$NAME.manifest" ] || die "GEANT4 $NAME is already released (it has a manifest); it must not change"
[ -f "$OPTIONS" ] || die "no cmake options $OPTIONS"

umask 022
mkdir -p "$WORKDIR"

options() { # the -D lines of the options file, without comments
    sed -e 's/#.*//' -e 's/[[:space:]]*$//' "$OPTIONS" | grep -v '^[[:space:]]*$'
}

configure() {
    local tarball="$WORKDIR/geant4-v$VER.tar.gz"
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
    cmake --fresh -S "$SRC" -B "$BUILD" -DCMAKE_INSTALL_PREFIX="$PREFIX" "${opts[@]}" \
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
    log "GEANT4 $NAME is ready ($(du -sh "$PREFIX" | cut -f1)). Next: copy it to the server with"
    copy_hint "$PREFIX"
}

case "$STAGE" in
    configure) configure ;;
    build) build; check ;;
    check) check ;;
    all) configure; build; check ;;
esac
