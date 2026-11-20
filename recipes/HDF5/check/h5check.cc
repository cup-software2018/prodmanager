// Check of an HDF5 installation, built and run by recipes/HDF5/build.sh ('check' stage).
//
// Uses the library the way the DAQ does: a compound type with an array member, a chunked, deflated
// dataset with an unlimited dimension, appended block by block through hyperslabs, then read back.
// Also links the high-level (H5LT) and C++ (H5Cpp) libraries.
//
//   h5check <file>

#include <H5Cpp.h>
#include <hdf5.h>
#include <hdf5_hl.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

namespace {

struct Hit {
    uint32_t ch;
    uint16_t adc[4];
    uint64_t time;
};

const hsize_t kBlock = 100;
const int kBlocks = 10;

int fail(const char* what)
{
    std::fprintf(stderr, "h5check: %s\n", what);
    return 1;
}

hid_t hit_type()
{
    hsize_t adims[1] = {4};
    hid_t adc = H5Tarray_create2(H5T_NATIVE_USHORT, 1, adims);
    hid_t t = H5Tcreate(H5T_COMPOUND, sizeof(Hit));
    H5Tinsert(t, "ch", HOFFSET(Hit, ch), H5T_NATIVE_UINT32);
    H5Tinsert(t, "adc", HOFFSET(Hit, adc), adc);
    H5Tinsert(t, "time", HOFFSET(Hit, time), H5T_NATIVE_ULLONG);
    H5Tclose(adc);
    return t;
}

Hit make_hit(uint64_t i)
{
    Hit h;
    h.ch = static_cast<uint32_t>(i % 64);
    for (int k = 0; k < 4; ++k) h.adc[k] = static_cast<uint16_t>(i * 4 + k);
    h.time = i * 1000003ULL;
    return h;
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 2) return fail("usage: h5check <file>");
    const char* fn = argv[1];
    if (H5Zfilter_avail(H5Z_FILTER_DEFLATE) <= 0) return fail("no deflate filter");

    // write
    hid_t f = H5Fcreate(fn, H5F_ACC_TRUNC, H5P_DEFAULT, H5P_DEFAULT);
    if (f == H5I_INVALID_HID) return fail("cannot create the file");
    hid_t t = hit_type();
    hsize_t dims[1] = {0}, maxdims[1] = {H5S_UNLIMITED}, chunk[1] = {256};
    hid_t space = H5Screate_simple(1, dims, maxdims);
    hid_t dcpl = H5Pcreate(H5P_DATASET_CREATE);
    H5Pset_chunk(dcpl, 1, chunk);
    H5Pset_deflate(dcpl, 6);
    hid_t g = H5Gcreate2(f, "/run", H5P_DEFAULT, H5P_DEFAULT, H5P_DEFAULT);
    hid_t d = H5Dcreate2(g, "hits", t, space, H5P_DEFAULT, dcpl, H5P_DEFAULT);
    if (d == H5I_INVALID_HID) return fail("cannot create the dataset");
    std::vector<Hit> block(kBlock);
    for (int b = 0; b < kBlocks; ++b) {
        for (hsize_t i = 0; i < kBlock; ++i) block[i] = make_hit(b * kBlock + i);
        hsize_t size[1] = {(b + 1) * kBlock}, start[1] = {b * kBlock}, count[1] = {kBlock};
        H5Dset_extent(d, size);
        hid_t fs = H5Dget_space(d);
        H5Sselect_hyperslab(fs, H5S_SELECT_SET, start, nullptr, count, nullptr);
        hid_t ms = H5Screate_simple(1, count, nullptr);
        if (H5Dwrite(d, t, ms, fs, H5P_DEFAULT, block.data()) < 0) return fail("write failed");
        H5Sclose(ms);
        H5Sclose(fs);
    }
    if (H5LTset_attribute_string(f, "/run", "comment", "h5check") < 0) return fail("H5LT failed");
    H5Dclose(d);
    H5Gclose(g);
    H5Pclose(dcpl);
    H5Sclose(space);
    H5Fclose(f);

    // read back with the C API
    f = H5Fopen(fn, H5F_ACC_RDONLY, H5P_DEFAULT);
    d = H5Dopen2(f, "/run/hits", H5P_DEFAULT);
    if (d == H5I_INVALID_HID) return fail("cannot open the dataset");
    hid_t fs = H5Dget_space(d);
    hsize_t n[1];
    H5Sget_simple_extent_dims(fs, n, nullptr);
    if (n[0] != kBlocks * kBlock) return fail("wrong number of hits");
    hid_t plist = H5Dget_create_plist(d);
    bool deflated = false;
    for (int i = 0; i < H5Pget_nfilters(plist); ++i) {
        unsigned flags;
        size_t nelmts = 0;
        char name[64];
        if (H5Pget_filter2(plist, i, &flags, &nelmts, nullptr, sizeof name, name, nullptr) == H5Z_FILTER_DEFLATE)
            deflated = true;
    }
    if (!deflated) return fail("the dataset is not deflated");
    std::vector<Hit> all(n[0]);
    if (H5Dread(d, t, H5S_ALL, H5S_ALL, H5P_DEFAULT, all.data()) < 0) return fail("read failed");
    for (hsize_t i = 0; i < n[0]; ++i) {
        Hit e = make_hit(i);
        if (all[i].ch != e.ch || all[i].time != e.time || std::memcmp(all[i].adc, e.adc, sizeof e.adc) != 0)
            return fail("read back different values");
    }
    char comment[16] = "";
    H5LTget_attribute_string(f, "/run", "comment", comment);
    if (std::strcmp(comment, "h5check") != 0) return fail("wrong attribute");
    H5Pclose(plist);
    H5Sclose(fs);
    H5Dclose(d);
    H5Tclose(t);
    H5Fclose(f);

    // and with the C++ API
    H5::H5File cf(fn, H5F_ACC_RDONLY);
    if (cf.openDataSet("/run/hits").getSpace().getSimpleExtentNpoints() != static_cast<hssize_t>(n[0]))
        return fail("C++ API: wrong number of hits");

    unsigned maj, min, rel;
    H5get_libversion(&maj, &min, &rel);
    std::printf("h5check: HDF5 %u.%u.%u wrote and read back %llu hits (compound, chunked, deflate, H5LT, H5Cpp)\n",
                maj, min, rel, static_cast<unsigned long long>(n[0]));
    return 0;
}
