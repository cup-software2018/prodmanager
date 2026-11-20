#!/bin/sh
# Build record of CUPDAQ from its CMake package files, programs and libraries. Prints "key<TAB>value" lines.
dir="$1"
cfg="$dir/lib64/cmake/CUPDAQ/CUPDAQConfigVersion.cmake"
[ -r "$cfg" ] || { echo "no $cfg" >&2; exit 1; }
printf 'cmake package version\t%s\n' "$(sed -n 's/^set(PACKAGE_VERSION "\(.*\)")$/\1/p' "$cfg")"
for f in "$dir"/bin/* "$dir"/lib64/*.so; do
    [ -f "$f" ] && head -c4 "$f" | grep -q ELF || continue
    name="$(basename "$f")"
    printf '%s rpath\t%s\n' "$name" "$(readelf -d "$f" | sed -n 's/.*Library r[un]*path: \[\(.*\)\]$/\1/p')"
    printf '%s needs\t%s\n' "$name" "$(readelf -d "$f" | sed -n 's/.*Shared library: \[\(.*\)\]$/\1/p' | tr '\n' ' ' | sed 's/ *$//')"
done
