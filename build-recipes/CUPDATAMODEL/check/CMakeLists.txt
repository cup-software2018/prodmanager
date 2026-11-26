# Check of a CUPDATAMODEL installation, built by build-recipes/CUPDATAMODEL/build.sh ('check' stage). The package is
# found the way CUPDAQ finds it: find_package(CUPDataModel), which itself finds ROOT and HDF5.
cmake_minimum_required(VERSION 3.16)
project(cupdmcheck CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(CUPDataModel REQUIRED)
find_package(ROOT REQUIRED COMPONENTS Core RIO Tree)
message(STATUS "cupdmcheck: CUPDataModel ${CUPDataModel_VERSION} from ${CUPDataModel_DIR}")
message(STATUS "cupdmcheck: ROOT ${ROOT_VERSION} from ${ROOT_DIR}, HDF5 ${HDF5_VERSION}")

add_executable(cupdmcheck cupdmcheck.cc)
target_link_libraries(cupdmcheck PRIVATE CUPDataModel::HDF5Utils CUPDataModel::RawObjs ROOT::Tree)
