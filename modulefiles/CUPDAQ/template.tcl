#%Module
@HEADER@
# CUP DAQ. Same variables as the CMake-generated setup_cupdaq.sh in the version directory.
module-whatis "CUPDAQ @VERSION@"
conflict CUPDAQ
@DEPENDS@
setenv       CUPDAQ_DIR        @PREFIX@
prepend-path PATH              @PREFIX@/bin
prepend-path LD_LIBRARY_PATH   @PREFIX@/lib64
prepend-path ROOT_INCLUDE_PATH @PREFIX@/include
