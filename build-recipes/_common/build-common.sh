# Helpers for build-recipes/<NAME>/build.sh (sourced by them; bash).
#
#   with_progress <what> <log> <command...>
#       Runs the command with its output in <log> and, while it runs, prints a line every minute:
#           build.sh: compiling 45% at 12:31 (23 min)
#       The percentage is the last "[ NN%]" that cmake's Makefiles wrote into the log. A build.sh may define
#       progress_hint() (<log> -> one line of text) after sourcing this file, to describe the progress otherwise.
#       The command runs in the foreground, so Ctrl-C stops it as before. Returns the command's exit status.
#
#   missing_libs <prefix>
#       Prints "<file>: <library>" for each shared library that the ELF files under <prefix> need and ldd does
#       not find, with the rule of envcheck (release, sync, verify): a library lying in the same directory as the file is not
#       missing (Geant4's libraries have no RPATH and find each other through LD_LIBRARY_PATH).
#
#   ready <recipe.yml> <prefix> <Products>
#       The end of a successful build: saves the recipe next to the version as <prefix>.recipe.yml
#       (save-recipe.py; `prodman release` reads it there, so no tool upgrade is needed), then says what comes next.
#
#   copy_hint <prefix>
#       Prints the rsync command that copies the prefix and its saved recipe to the products server.
#
#   read_recipe <recipe.yml>
#       Checks the recipe (recipeinfo.py) and sets SRC_URL and SRC_TAG (its source), OPTIONS (array: its options)
#       and RECIPE_DEPENDS (array: NAME/VERSION of its depends).
#
#   load_depends <recipe.yml> <Products> <workdir>
#       Loads the modulefiles of the recipe's depends (environment-modules), and nothing else. Released ones are
#       the fixed modulefiles in <Products>/modulefiles; staging ones are made from the repository's templates
#       into <workdir>/modulefiles (depmodules.py). Sets DEPS and DEP_LIBDIRS: the directories the modulefiles
#       put on LD_LIBRARY_PATH.
#
#   fetch_source <dir>
#       Puts the recipe's source (read_recipe) into <dir>: the git tag SRC_TAG of the repository SRC_URL, or the
#       archive SRC_URL, downloaded into $WORKDIR (kept there: SRC_ARCHIVE) and unpacked.
#
#   unresolve_rpath <prefix>
#       Where Products is a symlink, ROOT's CMake files give its library directory with the symlink resolved
#       (ROOTConfig.cmake uses REALPATH), and a project that links with ${ROOT_USE_FILE} gets that directory in
#       its RPATH, before the right one. This removes, from the ELF files under <prefix>, every RPATH entry that
#       is one of DEP_LIBDIRS with symlinks resolved.
#
#   check_rpath <prefix>
#       Fails unless every RPATH entry of the ELF files under <prefix> is $ORIGIN-relative or one of DEP_LIBDIRS.
#
#   check_root_classes <libdir> <log>
#       Interactive ROOT must load every class in the rootmap files of <libdir> by name (rootmap -> library ->
#       dictionary), without errors or warnings. Output goes to <log>.

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

progress_hint() { # the last cmake percentage in the log, e.g. "45%"
    grep -o '^\[ *[0-9]*%\]' "$1" 2>/dev/null | tail -1 | tr -d '[] '
}

with_progress() {
    local what="$1" log="$2"
    shift 2
    local t0 rc=0 reporter
    t0="$(date +%s)"
    (
        # not build.sh's set -e: a log with nothing to report yet (grep finds no match) must not end the reporter
        set +e +o pipefail
        # stopped by the kill below; the sleep goes with it
        trap 'kill $! 2>/dev/null; exit 0' TERM
        while :; do
            sleep 60 & wait $!
            kill -0 $$ 2>/dev/null || exit 0 # build.sh itself was stopped
            hint="$(progress_hint "$log")"
            echo "build.sh: $what${hint:+ $hint} at $(date +%H:%M) ($(( ($(date +%s) - t0) / 60 )) min)"
        done
    ) &
    reporter=$!
    "$@" > "$log" 2>&1 || rc=$?
    kill "$reporter" 2>/dev/null || true
    wait "$reporter" 2>/dev/null || true
    return "$rc"
}

missing_libs() {
    find "$1" -type f \( -name '*.so*' -o -perm -u+x \) -exec sh -c '
        for f; do
            head -c4 "$f" | grep -q ELF || continue
            ldd "$f" 2>/dev/null | sed -n "s|^\t\(.*\) => not found|\1|p" | while read -r lib; do
                [ -e "$(dirname "$f")/$lib" ] || echo "$f: $lib"
            done
        done
        exit 0' _ {} + | sort -u
}

copy_hint() {
    # The destination is the server's real path: rsync 3.2.7-1.el9_8 as the receiving side refuses a destination
    # that goes through a symlink ("Too many levels of symbolic links"), and /home/cupsoft/Products is one.
    local parent
    parent="$(dirname "$1")"
    log "  rsync -a $1 $1.recipe.yml <server>:<$parent with symlinks resolved on the server>/"
    log "  (find that path with: ssh <server> readlink -f $parent)"
}

ready() {
    local recipe="$1" prefix="$2" products="$3"
    /usr/bin/python3 -I "$COMMON_DIR/save-recipe.py" "$recipe" --products_dir "$products" | sed 's/^save-recipe:/build.sh:/' \
        || die "could not save the recipe next to $prefix"
    log "$(basename "$(dirname "$prefix")") $(basename "$prefix") is ready ($(du -sh "$prefix" | cut -f1))."
    log "Next, on the products server: add it to a vlist draft, then run"
    log "  prodman release <vlist> --check && prodman release <vlist>"
    log "If you built it on another node, copy it to the server first:"
    copy_hint "$prefix"
}

read_recipe() {
    local recipe="$1" out kind a b
    out="$(/usr/bin/python3 -I "$COMMON_DIR/recipeinfo.py" "$recipe")" || die "cannot read the recipe $recipe"
    SRC_URL="" SRC_TAG="" OPTIONS=() RECIPE_DEPENDS=()
    while IFS=$'\t' read -r kind a b; do
        case "$kind" in
            url) SRC_URL="$a" ;;
            tag) SRC_TAG="$a" ;;
            option) OPTIONS+=("$a") ;;
            depends) RECIPE_DEPENDS+=("$a/$b") ;;
        esac
    done <<< "$out"
}

load_depends() {
    local recipe="$1" products="$2" workdir="$3" out kind a
    [ -r /usr/share/Modules/init/bash ] || die "environment-modules is not installed"
    rm -rf "$workdir/modulefiles"
    out="$(/usr/bin/python3 -I "$COMMON_DIR/depmodules.py" "$recipe" "$products" "$workdir/modulefiles")" \
        || die "cannot get the modulefiles of the dependencies of $recipe"
    set +eu # the init script and module do not expect -e or -u
    . /usr/share/Modules/init/bash
    module purge
    set -eu
    export MODULEPATH="$products/modulefiles"
    [ ! -d "$workdir/modulefiles" ] || MODULEPATH="$workdir/modulefiles:$MODULEPATH"
    DEPS=()
    while IFS=$'\t' read -r kind a; do
        case "$kind" in
            module) DEPS+=("$a"); module load "$a" || die "module load $a failed" ;;
        esac
    done <<< "$out"
    log "dependencies: $(module -t list 2>&1 | grep -v ':$' | tr '\n' ' ')"
    IFS=: read -ra DEP_LIBDIRS <<< "${LD_LIBRARY_PATH:-}"
}

fetch_source() {
    local dir="$1"
    [ -n "$SRC_URL" ] || die "the recipe has no source"
    if [ -n "$SRC_TAG" ]; then
        _fetch_git "$SRC_URL" "$SRC_TAG" "$dir"
    else
        _fetch_archive "$SRC_URL" "$dir"
    fi
}

_fetch_git() { # <url> <tag> <dir>: exactly the tag (never a branch of the same name), fresh every time
    local url="$1" tag="$2" dir="$3"
    rm -rf "$dir"
    git init -q "$dir"
    log "fetching tag $tag of $url"
    git -C "$dir" fetch -q --depth 1 "$url" "refs/tags/$tag:refs/tags/$tag" || die "no tag $tag in $url"
    git -C "$dir" -c advice.detachedHead=false checkout -q "refs/tags/$tag" || die "cannot check out tag $tag"
    log "source $url tag $tag ($(git -C "$dir" log -1 --format='%cs %s'))"
}

_fetch_archive() { # <url> <dir>: downloaded once into $WORKDIR; unpacked once, without the archive's top directory
    local url="$1" dir="$2" top
    SRC_ARCHIVE="$WORKDIR/$(basename "$url")"
    if [ ! -f "$SRC_ARCHIVE" ]; then
        log "downloading $url"
        curl -fL --retry 3 -o "$SRC_ARCHIVE.part" "$url" || die "cannot download $url"
        mv "$SRC_ARCHIVE.part" "$SRC_ARCHIVE"
    fi
    log "source sha256 $(sha256sum "$SRC_ARCHIVE" | cut -d' ' -f1)"
    sha256sum "$SRC_ARCHIVE" | sed "s|$WORKDIR/||" > "$WORKDIR/source.sha256"
    [ ! -d "$dir" ] || return 0
    rm -rf "$dir.part"
    mkdir -p "$dir.part"
    tar -C "$dir.part" -xf "$SRC_ARCHIVE" || die "cannot unpack $SRC_ARCHIVE"
    # go down while there is a single directory and nothing else (hdf5-2.x/ even holds ./hdf5-2.x/)
    top="$dir.part"
    while [ "$(find "$top" -mindepth 1 -maxdepth 1 | wc -l)" = 1 ] && [ -d "$(find "$top" -mindepth 1 -maxdepth 1)" ]; do
        top="$(find "$top" -mindepth 1 -maxdepth 1)"
    done
    mv "$top" "$dir"
    rm -rf "$dir.part"
}

_rpath_of() { readelf -d "$1" 2>/dev/null | sed -n 's/.*Library r[un]*path: \[\(.*\)\]$/\1/p'; }

_elf_files() { # ELF files under <prefix>, one per line
    find "$1" -type f \( -name '*.so*' -o -perm -u+x \) -exec sh -c '
        for f; do head -c4 "$f" | grep -q ELF && echo "$f"; done; exit 0' _ {} +
}

unresolve_rpath() {
    local prefix="$1" d f rp e new n=0
    local -A resolved=() # a dependency's library directory with symlinks resolved, where that differs
    for d in "${DEP_LIBDIRS[@]}"; do
        [ -n "$d" ] && [ "$(readlink -f "$d")" != "$d" ] && resolved["$(readlink -f "$d")"]="$d"
    done
    [ "${#resolved[@]}" -gt 0 ] || return 0
    while read -r f; do
        rp="$(_rpath_of "$f")"
        [ -n "$rp" ] || continue
        new=""
        IFS=: read -ra entries <<< "$rp"
        for e in "${entries[@]}"; do
            [ -z "${resolved[$e]:-}" ] || continue
            new="${new:+$new:}$e"
        done
        [ "$new" != "$rp" ] || continue
        cmake -DFILE="$f" -DNEW_RPATH="$new" -P "$COMMON_DIR/rpath-set.cmake" > /dev/null
        n=$((n + 1))
    done < <(_elf_files "$prefix")
    [ "$n" = 0 ] || log "RPATH: removed dependency library directories with symlinks resolved from $n files"
}

check_rpath() {
    local prefix="$1" f rp e ok d bad=0
    while read -r f; do
        rp="$(_rpath_of "$f")"
        [ -n "$rp" ] || continue
        IFS=: read -ra entries <<< "$rp"
        for e in "${entries[@]}"; do
            ok=0
            case "$e" in '$ORIGIN'*) ok=1 ;; esac
            for d in "${DEP_LIBDIRS[@]}"; do [ "$e" = "$d" ] && ok=1; done
            [ "$ok" = 1 ] || { echo "  ${f#"$prefix"/}: unexpected RPATH entry $e"; bad=1; }
        done
    done < <(_elf_files "$prefix")
    [ "$bad" = 0 ] || die "RPATH entries that are neither \$ORIGIN nor a dependency's library directory (above)"
    log "RPATH: only \$ORIGIN and the dependencies' library directories"
}

check_root_classes() {
    local libdir="$1" log="$2"
    root -l -b -q -n "$COMMON_DIR/rootcheck.C(\"$libdir\")" > "$log" 2>&1 || { tail -20 "$log"; die "root failed"; }
    grep '^rootcheck:' "$log" | sed 's/^/  /'
    grep -q '^rootcheck: OK' "$log" || die "ROOT cannot load every class by name (see $log)"
    if grep -E 'Error in <|Warning in <' "$log"; then
        die "ROOT reported errors or warnings (above, in $log)"
    fi
}
