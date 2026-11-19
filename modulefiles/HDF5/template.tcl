#%Module
@HEADER@
# HDF5. Legacy: HDF5/setup_HDF5.sh, whose last line put $PATH into CMAKE_PREFIX_PATH (fixed here).
module-whatis "HDF5 @VERSION@"
conflict HDF5
@DEPENDS@
setenv       HDF5SYS           @PREFIX@
prepend-path PATH              @PREFIX@/bin
prepend-path LD_LIBRARY_PATH   @PREFIX@/lib
prepend-path CMAKE_PREFIX_PATH @PREFIX@
