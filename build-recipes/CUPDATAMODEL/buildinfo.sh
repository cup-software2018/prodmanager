#!/bin/sh
# Build record of CUPDATAMODEL from its CMake package files and libraries. Prints "key<TAB>value" lines.
dir="$1"
cfg="$dir/lib64/cmake/CUPDataModel/CUPDataModelConfigVersion.cmake"
[ -r "$dir/lib64/cmake/CUPDataModel/CUPDataModelConfig.cmake" ] || { echo "no $dir/lib64/cmake/CUPDataModel/CUPDataModelConfig.cmake" >&2; exit 1; }
if [ -r "$cfg" ]; then
    printf 'cmake package version\t%s\n' "$(sed -n 's/^set(PACKAGE_VERSION "\(.*\)")$/\1/p' "$cfg")"
else  # CUPDATAMODEL V1.4 and later set no project version, so there is no CUPDataModelConfigVersion.cmake
    printf 'cmake package version\t%s\n' "none (no CUPDataModelConfigVersion.cmake)"
fi
for lib in "$dir"/lib64/*.so; do
    [ -e "$lib" ] || continue
    name="$(basename "$lib")"
    printf '%s rpath\t%s\n' "$name" "$(readelf -d "$lib" | sed -n 's/.*Library r[un]*path: \[\(.*\)\]$/\1/p')"
    printf '%s needs\t%s\n' "$name" "$(readelf -d "$lib" | sed -n 's/.*Shared library: \[\(.*\)\]$/\1/p' | tr '\n' ' ' | sed 's/ *$//')"
done
