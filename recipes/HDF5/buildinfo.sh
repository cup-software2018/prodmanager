#!/bin/sh
# Build record of HDF5 from lib/libhdf5.settings. Prints "key<TAB>value" lines.
settings="$1/lib/libhdf5.settings"
[ -r "$settings" ] || { echo "no $settings" >&2; exit 1; }
sed -n 's/^[[:space:]]*\([^:]*[^[:space:]:]\):[[:space:]]*\(..*\)$/\1\t\2/p' "$settings"
