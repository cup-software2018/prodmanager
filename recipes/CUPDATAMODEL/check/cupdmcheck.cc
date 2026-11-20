// Check of a CUPDATAMODEL installation, built and run by recipes/CUPDATAMODEL/build.sh ('check' stage).
//
// Uses the libraries the way the DAQ does and compares everything read back with what was written:
//   - HDF5Utils: SADC and FADC events written with H5DataWriter (several buffer flushes), read with H5DataReader
//   - RawObjs:   a TTree of FChannelData written to a ROOT file and read back
//
//   cupdmcheck <dir>

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include <TFile.h>
#include <TTree.h>

#include "FChannel.hh"
#include "FChannelData.hh"
#include "H5DataReader.hh"
#include "H5DataWriter.hh"
#include "H5FADCEvent.hh"
#include "H5SADCEvent.hh"

namespace {

const int kSadcEvents = 2000;
const int kFadcEvents = 300;
const int kFadcNdp = 128;
const int kTreeEvents = 100;

int fail(const std::string& what)
{
    std::fprintf(stderr, "cupdmcheck: %s\n", what.c_str());
    return 1;
}

EventInfo_t make_info(int i, int nhit)
{
    EventInfo_t info{};
    info.ttype = static_cast<std::uint16_t>(i % 3);
    info.nhit = static_cast<std::uint16_t>(nhit);
    info.tnum = static_cast<std::uint32_t>(i);
    info.ttime = static_cast<std::uint64_t>(i) * 5000ULL;
    return info;
}

bool same_info(const EventInfo_t& a, const EventInfo_t& b)
{
    return a.ttype == b.ttype && a.nhit == b.nhit && a.tnum == b.tnum && a.ttime == b.ttime;
}

int sadc_nhit(int i) { return 1 + i % 8; }

AChannel_t sadc_channel(int i, int ch)
{
    AChannel_t c{};
    c.id = static_cast<std::uint16_t>(ch);
    c.tbit = static_cast<std::uint16_t>(ch % 2);
    c.adc = static_cast<std::uint32_t>((i * 31 + ch * 7) % 16384);
    c.time = static_cast<std::uint32_t>(i * 5000 + ch);
    return c;
}

int fadc_nhit(int i) { return 1 + i % 4; }

std::uint16_t fadc_sample(int i, int ch, int s) { return static_cast<std::uint16_t>((i + ch * 3 + s) % 4096); }

int check_sadc(const std::string& fn)
{
    auto* w = new H5SADCEvent();
    w->SetBufferCapacity(100);
    {
        H5DataWriter writer(fn.c_str());
        writer.SetData(w);
        writer.SetSubrun(7);
        if (!writer.Open()) return fail("SADC: cannot open " + fn + " for writing");
        for (int i = 0; i < kSadcEvents; ++i) {
            std::vector<AChannel_t> chs;
            for (int ch = 0; ch < sadc_nhit(i); ++ch) chs.push_back(sadc_channel(i, ch));
            if (w->AppendEvent(make_info(i, 0), chs) < 0) return fail("SADC: AppendEvent failed");
        }
        writer.Close();
    }
    delete w;

    auto* r = new H5SADCEvent();
    H5DataReader reader;
    reader.Add(fn.c_str());
    reader.SetData(r);
    if (!reader.Open()) return fail("SADC: cannot open " + fn + " for reading");
    if (reader.GetEntries() != kSadcEvents) return fail("SADC: wrong number of events");
    for (int i = 0; i < kSadcEvents; ++i) {
        if (r->ReadEvent(i) < 0) return fail("SADC: ReadEvent failed");
        if (!same_info(r->GetEventInfo(), make_info(i, sadc_nhit(i)))) return fail("SADC: event info differs");
        const AChannel_t* d = r->GetData();
        for (int ch = 0; ch < sadc_nhit(i); ++ch) {
            AChannel_t e = sadc_channel(i, ch);
            if (d[ch].id != e.id || d[ch].tbit != e.tbit || d[ch].adc != e.adc || d[ch].time != e.time)
                return fail("SADC: channel data differs");
        }
    }
    reader.Close();
    delete r;
    return 0;
}

int check_fadc(const std::string& fn)
{
    auto* w = new H5FADCEvent();
    w->SetNDP(kFadcNdp);
    w->SetBufferCapacity(50);
    {
        H5DataWriter writer(fn.c_str());
        writer.SetData(w);
        writer.SetSubrun(7);
        if (!writer.Open()) return fail("FADC: cannot open " + fn + " for writing");
        for (int i = 0; i < kFadcEvents; ++i) {
            std::vector<FChannel_t> chs(fadc_nhit(i));
            for (int ch = 0; ch < fadc_nhit(i); ++ch) {
                chs[ch].id = static_cast<std::uint16_t>(ch);
                chs[ch].ped = static_cast<std::uint16_t>(100 + ch);
                chs[ch].time = static_cast<std::uint32_t>(i * 5000 + ch);
                for (int s = 0; s < kFadcNdp; ++s) chs[ch].waveform[s] = fadc_sample(i, ch, s);
            }
            if (w->AppendEvent(make_info(i, 0), chs) < 0) return fail("FADC: AppendEvent failed");
        }
        writer.Close();
    }
    delete w;

    auto* r = new H5FADCEvent();
    H5DataReader reader;
    reader.Add(fn.c_str());
    reader.SetData(r);
    if (!reader.Open()) return fail("FADC: cannot open " + fn + " for reading");
    if (reader.GetEntries() != kFadcEvents) return fail("FADC: wrong number of events");
    for (int i = 0; i < kFadcEvents; ++i) {
        if (r->ReadEvent(i) < 0) return fail("FADC: ReadEvent failed");
        if (!same_info(r->GetEventInfo(), make_info(i, fadc_nhit(i)))) return fail("FADC: event info differs");
        const FChannel_t* d = r->GetData();
        for (int ch = 0; ch < fadc_nhit(i); ++ch) {
            if (d[ch].id != ch || d[ch].ped != 100 + ch || d[ch].time != static_cast<std::uint32_t>(i * 5000 + ch))
                return fail("FADC: channel header differs");
            for (int s = 0; s < kFadcNdp; ++s)
                if (d[ch].waveform[s] != fadc_sample(i, ch, s)) return fail("FADC: waveform differs");
        }
    }
    if (r->GetNDP() != kFadcNdp) return fail("FADC: wrong number of samples per waveform");
    reader.Close();
    delete r;
    return 0;
}

int check_tree(const std::string& fn)
{
    {
        TFile f(fn.c_str(), "RECREATE");
        if (f.IsZombie()) return fail("RawObjs: cannot create " + fn);
        TTree t("t", "cupdmcheck");
        auto* data = new FChannelData();
        t.Branch("fadc", &data);
        std::vector<unsigned short> wave(kFadcNdp);
        for (int i = 0; i < kTreeEvents; ++i) {
            data->Clear();
            for (int ch = 0; ch < fadc_nhit(i); ++ch) {
                for (int s = 0; s < kFadcNdp; ++s) wave[s] = fadc_sample(i, ch, s);
                data->Add(static_cast<unsigned short>(ch), kFadcNdp, wave.data());
            }
            t.Fill();
        }
        t.Write();
        delete data;
    }
    TFile f(fn.c_str());
    auto* t = f.Get<TTree>("t");
    if (!t || t->GetEntries() != kTreeEvents) return fail("RawObjs: wrong number of entries");
    FChannelData* data = nullptr;
    t->SetBranchAddress("fadc", &data);
    for (int i = 0; i < kTreeEvents; ++i) {
        t->GetEntry(i);
        if (!data || data->GetN() != fadc_nhit(i)) return fail("RawObjs: wrong number of channels");
        for (int ch = 0; ch < fadc_nhit(i); ++ch) {
            FChannel* c = data->Get(ch);
            if (c->GetNdp() != kFadcNdp) return fail("RawObjs: wrong number of samples");
            for (int s = 0; s < kFadcNdp; ++s)
                if (c->GetWaveform()[s] != fadc_sample(i, ch, s)) return fail("RawObjs: waveform differs");
        }
    }
    return 0;
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 2) return fail("usage: cupdmcheck <dir>");
    const std::string dir = argv[1];
    if (check_sadc(dir + "/sadc.h5") != 0) return 1;
    std::printf("cupdmcheck: HDF5Utils: %d SADC events written and read back\n", kSadcEvents);
    if (check_fadc(dir + "/fadc.h5") != 0) return 1;
    std::printf("cupdmcheck: HDF5Utils: %d FADC events (%d samples) written and read back\n", kFadcEvents, kFadcNdp);
    if (check_tree(dir + "/rawobjs.root") != 0) return 1;
    std::printf("cupdmcheck: RawObjs: TTree of %d FChannelData events written and read back\n", kTreeEvents);
    return 0;
}
