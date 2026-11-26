#!/bin/sh
# Build record of CAENHVWRAPPER: the library file and its sha256. Prints "key<TAB>value" lines.
dir="$1"
lib="$(ls "$dir"/lib/libcaenhvwrapper.so.* 2>/dev/null | head -1)"
[ -n "$lib" ] || { echo "no $dir/lib/libcaenhvwrapper.so.*" >&2; exit 1; }
printf 'library\t%s\n' "$(basename "$lib")"
printf 'sha256\t%s\n' "$(sha256sum "$lib" | cut -d' ' -f1)"
