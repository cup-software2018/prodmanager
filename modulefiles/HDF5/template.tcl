#%Module
@HEADER@
# HDF5.
module-whatis "HDF5 @VERSION@"
conflict HDF5
@DEPENDS@
setenv       HDF5SYS           @PREFIX@
prepend-path PATH              @PREFIX@/bin
prepend-path LD_LIBRARY_PATH   @PREFIX@/lib
prepend-path CMAKE_PREFIX_PATH @PREFIX@
