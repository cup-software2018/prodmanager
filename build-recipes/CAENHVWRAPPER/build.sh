#!/bin/bash
# Install CAENHVWRAPPER <VERSION> at its final path <Products>/CAENHVWRAPPER/<VERSION> (staging, before
# `prodman release`).
#
#   build-recipes/CAENHVWRAPPER/build.sh <version> [--workdir DIR] [--products_dir DIR]
#
#   <version> is given without the V: `build.sh 6.6` installs build-recipes/CAENHVWRAPPER/V6.6.yml into
#   <Products>/CAENHVWRAPPER/V6.6 (<VERSION> below is V<version>).
#
# CAEN ships the library prebuilt, and its download needs a login: put the archive from CAEN next to the recipe,
# as build-recipes/CAENHVWRAPPER/CAENHVWrapper-<version>.tgz (e.g. CAENHVWrapper-6.6.tgz). build.sh unpacks it and
# runs CAEN's install.sh with the prefix (x86_64 library, header, documents), then checks the installation: a
# small program built against it prints CAENHVLibSwRel(), and no shared library may be missing.
#
# - Runs in a clean environment (no variables from a loaded vlist).
# - Refuses a version that is already frozen (it has a manifest), and a non-empty prefix.
# - The unpacked archive, the check and the logs go to --workdir (default /tmp/prod-build-caenhvwrapper-<VERSION>),
#   never under Products.
# At the end the recipe is saved next to the version, <Products>/CAENHVWRAPPER/<VERSION>.recipe.yml;
# `prodman release` reads it there.

set -euo pipefail

if [ -z "${_PROD_BUILD_CLEAN:-}" ]; then
    exec env -i HOME="$HOME" USER="${USER:-$(id -un)}" PATH=/usr/bin:/bin LANG=C.UTF-8 TERM="${TERM:-dumb}" \
        _PROD_BUILD_CLEAN=1 bash "$0" "$@"
fi

die() { echo "build.sh: $*" >&2; exit 1; }
log() { echo "build.sh: $*"; }

usage() { sed -n '2,/^[^#]/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/../_common/build-common.sh" # missing_libs, ready

ARG=""
WORKDIR=""
PRODUCTS="/home/cupsoft/Products"

while [ $# -gt 0 ]; do
    case "$1" in
        --workdir) WORKDIR="${2:?}"; shift 2 ;;
        --products_dir) PRODUCTS="${2:?}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        -*) usage >&2; die "unknown option: $1" ;;
        *) [ -z "$ARG" ] || die "only one version"; ARG="$1"; shift ;;
    esac
done

[ -n "$ARG" ] || { usage >&2; die "give the version without the V, e.g. 6.6"; }
[[ "$ARG" =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "the version must look like 6.6 (without the V): $ARG"
NAME="V$ARG"
PREFIX="$PRODUCTS/CAENHVWRAPPER/$NAME"
RECIPE="$HERE/$NAME.yml"
ARCHIVE="$HERE/CAENHVWrapper-$ARG.tgz"
WORKDIR="${WORKDIR:-${TMPDIR:-/tmp}/prod-build-caenhvwrapper-$NAME}"
case "$WORKDIR" in /*) ;; *) WORKDIR="$PWD/$WORKDIR" ;; esac  # absolute: the steps run in other directories
LIB="$PREFIX/lib/libcaenhvwrapper.so.$ARG"

[ -d "$PRODUCTS" ] || die "$PRODUCTS does not exist"
[ ! -e "$PRODUCTS/CAENHVWRAPPER/$NAME.manifest" ] \
    || die "CAENHVWRAPPER $NAME is already released (it has a manifest); it must not change"
[ -f "$RECIPE" ] || die "no recipe $RECIPE"
[ -f "$ARCHIVE" ] || die "no $ARCHIVE: put the archive from CAEN there"
if [ -d "$PREFIX" ] && [ -n "$(ls -A "$PREFIX")" ]; then
    die "$PREFIX is not empty; remove it first to install again (chmod -R u+w if it was locked)"
fi

umask 022
read_recipe "$RECIPE" # checks the recipe
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

log "source $(basename "$ARCHIVE"), sha256 $(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
mkdir "$WORKDIR/src"
tar -C "$WORKDIR/src" -xzf "$ARCHIVE" || die "cannot unpack $ARCHIVE"
src="$(find "$WORKDIR/src" -mindepth 1 -maxdepth 1 -type d)"
[ -f "$src/install.sh" ] && [ "$(echo "$src" | wc -l)" = 1 ] || die "$ARCHIVE has no single top directory with install.sh"

log "CAEN's install.sh into $PREFIX (log: $WORKDIR/install.log)"
mkdir -p "$PREFIX"
(cd "$src" && sh ./install.sh "$PREFIX") > "$WORKDIR/install.log" 2>&1 \
    || { tail -20 "$WORKDIR/install.log"; die "install.sh failed, see $WORKDIR/install.log"; }
[ -f "$LIB" ] || die "no $LIB after install.sh: is the archive version $ARG?"
[ -f "$PREFIX/include/CAENHVWrapper.h" ] || die "no $PREFIX/include/CAENHVWrapper.h after install.sh"

log "building a check program against the installation (log: $WORKDIR/check.log)"
cat > "$WORKDIR/check.c" <<'EOF'
#include <stdio.h>
#include "CAENHVWrapper.h"
int main(void) { printf("CAENHVLibSwRel %s\n", CAENHVLibSwRel()); return 0; }
EOF
# the variables of the CAENHVWRAPPER modulefile, nothing else
env -i HOME="$HOME" LANG=C.UTF-8 PATH=/usr/bin:/bin CAENHVWRAPPER_HOME="$PREFIX" LD_LIBRARY_PATH="$PREFIX/lib" \
    bash --noprofile --norc -c '
    set -e
    gcc -DUNIX -DLINUX -I"$CAENHVWRAPPER_HOME/include" -o "$1/check" "$1/check.c" -L"$CAENHVWRAPPER_HOME/lib" -lcaenhvwrapper
    "$1/check"
    ldd "$1/check" | grep caenhvwrapper' _ "$WORKDIR" > "$WORKDIR/check.log" 2>&1 \
    || { tail -20 "$WORKDIR/check.log"; die "the check program failed"; }
grep '^CAENHVLibSwRel' "$WORKDIR/check.log" | sed 's/^/build.sh:   /'
grep -q "=> $PREFIX/lib/libcaenhvwrapper.so" "$WORKDIR/check.log" \
    || die "the check program runs with a CAENHVWRAPPER library outside $PREFIX (see $WORKDIR/check.log)"

log "looking for missing shared libraries"
missing="$(missing_libs "$PREFIX")"
if [ -n "$missing" ]; then
    echo "$missing" | head -20
    die "$(echo "$missing" | wc -l) missing shared libraries (above)"
fi
ready "$RECIPE" "$PREFIX" "$PRODUCTS"
