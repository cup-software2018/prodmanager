#!/bin/sh
# Build record of Geant4 from geant4-config. Prints "key<TAB>value" lines.
gc="$1/bin/geant4-config"
[ -x "$gc" ] || { echo "no $gc" >&2; exit 1; }
printf 'geant4-config --version\t%s\n' "$("$gc" --version)"
printf 'geant4-config --cflags\t%s\n' "$("$gc" --cflags | tr '\n' ' ' | sed 's/ *$//')"
for f in multithreading staticlibs clhep expat zlib gdml usolids freetype hdf5 qt motif opengl-x11 \
         raytracer-x11 vrml openinventor ptl tbb vecgeom smartstack; do
    printf 'has-feature %s\t%s\n' "$f" "$("$gc" --has-feature "$f" 2>/dev/null || echo unknown)"
done
"$gc" --datasets | awk '{ n = split($3, p, "/"); printf "dataset %s\t%s\n", $1, p[n] }'
