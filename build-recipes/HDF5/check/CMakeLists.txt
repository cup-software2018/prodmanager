# Check of an HDF5 installation, built by build-recipes/HDF5/build.sh ('check' stage). HDF5 is found the way the
# DAQ finds it (find_package with the C, CXX and HL components; HDF5_INCLUDE_DIRS and HDF5_LIBRARIES).
cmake_minimum_required(VERSION 3.16)
project(h5check C CXX)

find_package(HDF5 REQUIRED COMPONENTS C CXX HL)
message(STATUS "h5check: HDF5 ${HDF5_VERSION}, include ${HDF5_INCLUDE_DIRS}")
message(STATUS "h5check: libraries ${HDF5_LIBRARIES} ${HDF5_HL_LIBRARIES}")

add_executable(h5check h5check.cc)
target_include_directories(h5check PRIVATE ${HDF5_INCLUDE_DIRS})
target_compile_definitions(h5check PRIVATE ${HDF5_DEFINITIONS})
target_link_libraries(h5check PRIVATE ${HDF5_LIBRARIES} ${HDF5_HL_LIBRARIES})
