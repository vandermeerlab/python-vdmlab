# Adapted from nlxio written by Bernard Willards <https://github.com/bwillers/nlxio>

import numpy as np
import vdmlab as vdm


def load_events(filename, labels):
    nev_data = load_nev(filename)

    idx = {label: [] for label in labels}
    for label in labels:
        for i, event in enumerate(nev_data['event_str']):
            if event.decode() == label:
                idx[label].append(i)

    timestamps = {label: [] for label in labels}

    times = nev_data['time'].astype(float) * 1e-6

    for label in labels:
        timestamps[label] = times[idx[label]]

    return timestamps


def load_lfp(filename):
    data, time = load_ncs(filename)

    return vdm.LocalFieldPotential(data, time)


def load_header(filename):
    """Loads a neuralynx header.

    Parameters
    ----------
    filename: str

    Returns
    -------
    header: byte str

    """
    f = open(filename, 'rb')

    # Nlx files have a 16kbyte header
    header = f.read(2 ** 14).strip(b'\x00')

    return header


def load_ncs(filename):
    """Loads a neuralynx .ncs electrode file.

    Parameters
    ----------
    filename: str

    Returns
    -------
    cscs: np.array
        Voltage trace (V)
    times: np.array
        Timestamps (microseconds)

    Usage
    -----
    csc, time = nlxio.loadNcs('TT4E1.ncs')

    """

    f = open(filename, 'rb')

    # Nlx files have a 16kbyte header
    header = f.read(2 ** 14).strip(b'\x00')

    # The format for a .ncs files according the the neuralynx docs is
    # uint64 - timestamp in microseconds
    # uint32 - channel number
    # uint32 - sample freq
    # uint32 - number of valid samples
    # int16 x 512 - actual csc samples
    dt = np.dtype([('time', '<Q'), ('channel', '<i'), ('freq', '<i'),
                   ('valid', '<i'), ('csc', '<h', (512,))])
    # five points for fast numpy dtype reading
    data = np.fromfile(f, dt)

    # unpack the csc matrix
    csc = data['csc'].reshape((data['csc'].size,))

    data_times = data['time'] * 1e-6

    # find the frequency
    frequency = np.unique(data['freq'])
    if len(frequency) > 1:
        raise IOError("only one frequency allowed")
    frequency = frequency[0]

    # .ncs files have a timestamp for every ~512 data points.
    # Here, we assign timestamps for each data sample based on the sampling frequency
    # for each of the 512 data points. Sometimes a block will have fewer than 512 data entries,
    # number is set in data['valid'].
    this_idx = 0
    n_block = 512.
    offsets = np.arange(0, n_block / frequency, 1. / frequency)
    times = np.zeros(csc.shape)
    for i, (time, n_valid) in enumerate(zip(data_times, data['valid'])):
        times[this_idx:this_idx + n_valid] = time + offsets[:n_valid]
        this_idx += n_valid

    # now find a2d conversion factor in the header
    volt_conversion_factor = None
    for line in header.split(b'\n'):
        if line.strip().startswith(b'-ADBitVolts'):
            volt_conversion_factor = np.array(float(line.split(b' ')[1].decode()))

    if volt_conversion_factor is None:
        raise IOError("ADBitVolts not found in .ncs header for " + filename)

    cscs = csc * volt_conversion_factor

    return cscs, times


def load_nev(filename):
    """Loads a neuralynx .nev file.

    Parameters
    ----------
    filename: str

    Returns
    -------
    nev_data: dict
        With time (uint64), id (uint16), nttl (uint16), and event_str (charx128) as the most usable keys.

    """

    f = open(filename, 'rb')

    # There's nothing useful in the header for .nev files, so skip past it
    f.seek(2 ** 14)

    # An event record is as follows:
    # int16 - nstx - reserved
    # int16 - npkt_id - id of the originating system
    # int16 - npkt_data_size - this value should always be 2
    # uint64 - timestamp, microseconds
    # int16 - nevent_id - ID value for event
    # int16 - nttl - decimal TTL value read from the TTL input port
    # int16 - ncrc - record crc check, not used in consumer applications
    # int16 - ndummy1 - reserved
    # int16 - ndummy2 - reserved
    # int32x8 - dnExtra - extra bit values for this event
    # string(128) - event string
    dt = np.dtype([('filler1', '<h', 3), ('time', '<Q'), ('id', '<h'),
                   ('nttl', '<h'), ('filler2', '<h', 3), ('extra', '<i', 8),
                   ('event_str', np.dtype('a128'))])
    nev_data = np.fromfile(f, dt)

    return nev_data


def load_ntt(filename):
    """Loads a neuralynx .ntt tetrode spike file.

    Parameters
    ----------
    filename: str

    Returns
    -------
    timestamps: np.array
        Spikes as (num_spikes, length_waveform, num_channels)
    spikes: np.array
        Spike times as uint64 (us)
    frequency: float
        Sampling frequency in waveforms (Hz)

    Usage:
    timestamps, spikes, frequency = nlxio.loadNtt('TT13.ntt')

    """

    f = open(filename, 'rb')

    # A tetrode spike record is as folows:
    # uint64 - timestamp                    bytes 0:8
    # uint32 - acquisition entity number    bytes 8:12
    # uint32 - classified cel number        bytes 12:16
    # 8 * uint32- params                    bytes 16:48
    # 32 * 4 * int16 - waveform points
    # hence total record size is 2432 bits, 304 bytes

    # header is 16kbyte, i.e. 16 * 2^10 = 2^14
    header = f.read(2 ** 14).strip(b'\x00')

    # Read the header and find the conversion factors / sampling frequency
    a2d_conversion = None
    frequency = None

    for line in header.split(b'\n'):
        if line.strip().startswith(b'-ADBitVolts'):
            a2d_conversion = np.array(float(line.split(b' ')[1].decode()))
        if line.strip().startswith(b'-SamplingFrequency'):
            frequency = float(line.split(b' ')[1].decode())

    f.seek(2 ** 14)  # start of the spike, records
    # Neuralynx write little endian for some dumb reason
    dt = np.dtype([('time', '<Q'), ('filer', '<i', 10),
                   ('spikes', np.dtype('<h'), (32, 4))])
    data = np.fromfile(f, dt)

    if a2d_conversion is None:
        raise IOError("ADBitVolts not found in .ntt header for " + filename)
    if frequency is None:
        raise IOError("Frequency not found in .ntt header for " + filename)

    return data['time'], data['spikes'] * a2d_conversion, frequency