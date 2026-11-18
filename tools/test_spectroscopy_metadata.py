import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from sxm_anfatec.spectroscopy_metadata import enriched_bytes, original_bytes, summaries, parse, export, instrument_fields, spectroscopy_type, typed_filename

DATA = (b';Delays 1/Aqu/3/4/dead: 0.3/0.06/0.1/0.02/0.00006\r\n'
        b'time\tdz\tBias\tTopo\r\n'
        b'0.1\t0\t1000\t-233\r\n0.2\t1\t1000\t-232\r\n0.3\t0\t1000\t-233\r\n')


class MetadataTests(unittest.TestCase):
    def test_types_follow_data_and_preserve_original_name(self):
        self.assertEqual(spectroscopy_type(DATA), 'z_sweep')
        mixed = DATA.replace(b'0.2\t1\t1000', b'0.2\t1\t2000')
        self.assertEqual(spectroscopy_type(mixed), 'mixed_sweep')
        bias = mixed.replace(b'0.2\t1', b'0.2\t0')
        self.assertEqual(spectroscopy_type(bias), 'bias_sweep')
        constant = DATA.replace(b'0.2\t1', b'0.2\t0')
        self.assertEqual(spectroscopy_type(constant), 'constant_axes')
        self.assertEqual(typed_filename('au111_angii_0004.dat', 'z_sweep'), 'au111_angii_0004_z_sweep.dat')

    def test_travel_includes_return_but_span_does_not(self):
        s = summaries(DATA)
        self.assertEqual(s['Sweep.dz.Span_nm'], 1)
        self.assertEqual(s['Sweep.dz.RecordedTravel_nm'], 2)
        self.assertEqual(s['Sweep.dz.NetChange_nm'], 0)
        self.assertEqual(s['Sweep.dz.DirectionLegs'], 2)
        self.assertEqual(s['Height.TopoMinusDzMean_nm'], -233)
        self.assertEqual(s['Acquisition.RecordedPoints'], 3)
        self.assertAlmostEqual(s['Timing.NominalPointAcquisitionSum_s'], .18)
        self.assertAlmostEqual(s['Acquisition.FirstToLastPoint_s'], .2)

    def test_enrichment_preserves_original_and_replaces_its_block(self):
        first = enriched_bytes(DATA, {'note': 'test\nnot a data row'})
        second = enriched_bytes(first, {'note': 'replacement'})
        self.assertEqual(original_bytes(first), DATA)
        self.assertEqual(original_bytes(second), DATA)
        self.assertEqual(second.count(b';Meta.Begin:'), 1)
        self.assertEqual(parse(first)[3], parse(DATA)[3])

    def test_matrix_and_malformed_data_refused(self):
        for text in (b'time\tdz\tBias\t123\ns\tnm\tmV\t456\n',
                     b'time\tdz\tBias\n1\t2\n'):
            with self.assertRaises(ValueError):
                parse(text)

    def test_missing_amplitude_unit_is_not_guessed(self):
        capture = {'trigger': {'kind': 'dde', 'utc': 'test'}, 'read_start': {'utc': 'test', 'monotonic_ns': 0},
                   'read_end': {'utc': 'test', 'monotonic_ns': 1}, 'trigger_to_read_ms': 0,
                   'gui': {'parameters': {'Multi Channel LockIn': {'Lia1': {'Amplitude': 20}}}}}
        s = instrument_fields(capture)
        self.assertEqual(s['GUI.Lockin.Lia1.ModulationAmplitudeDisplayed'], 20)
        self.assertIn('unavailable', s['GUI.Lockin.Lia1.ModulationAmplitudeUnit'])
        self.assertEqual(instrument_fields(capture, 'mV')['GUI.Lockin.Lia1.ModulationAmplitudeUnit'], 'mV')

    def test_nonmonotonic_time_does_not_produce_sweep_rate(self):
        s = summaries(DATA.replace(b'0.3\t0', b'0.1\t0'))
        self.assertFalse(s['Acquisition.TimeStrictlyIncreasing'])
        self.assertNotIn('Sweep.dz.MeanRecordedTravelRate_nm_per_s', s)


if __name__ == '__main__':
    unittest.main()
