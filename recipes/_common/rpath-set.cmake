# Set the RPATH of one ELF file, the way `cmake --install` does it (used by build-common.sh: unresolve_rpath).
#   cmake -DFILE=<file> -DNEW_RPATH=<rpath> -P rpath-set.cmake
file(RPATH_SET FILE "${FILE}" NEW_RPATH "${NEW_RPATH}")
