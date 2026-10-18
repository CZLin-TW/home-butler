import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('temperature', Path(__file__).parents[1] / 'agent/macos_temperature.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)


class TemperatureTests(unittest.TestCase):
    def test_decode_invalid_and_bounds(self):
        for value in [0, -1, 151, float('nan'), float('inf')]:
            self.assertIsNone(t.decode_temperature(b'flt ', struct.pack('<f', value)))
        self.assertEqual(t.decode_temperature(b'flt ', struct.pack('<f', 44.5)), 44.5)
        self.assertIsNone(t.decode_temperature(b'sp78', b'1234'))
        self.assertIsNone(t.decode_temperature(b'flt ', b'123'))

    def test_missing_max_is_null_and_reader_closed(self):
        class Fake:
            closed = False
            def read(self, key):
                if key == 'TCMz': raise OSError('absent')
                return 44.5
            def close(self): self.closed = True
        reader = Fake()
        result = t.sample(lambda: reader, sleep=lambda _: None)
        self.assertEqual(result['sensors']['TCMb']['temperature_c'], 44.5)
        self.assertIsNone(result['sensors']['TCMz']['temperature_c'])
        self.assertTrue(reader.closed)

    def test_power_is_averaged_and_bounded(self):
        for value in [0, -1, 501, float('nan'), float('inf')]:
            self.assertIsNone(t.decode_power(b'flt ', struct.pack('<f', value)))
        self.assertAlmostEqual(t.decode_power(b'flt ', struct.pack('<f', 3.25)), 3.25)
        class Fake:
            values = iter([3.0, None, 5.0, OSError('busy'), 4.0])
            def read(self, key):
                if key != 'PSTR': return 44.5
                value = next(self.values)
                if isinstance(value, Exception): raise value
                return value
            def close(self): pass
        waits = []
        result = t.sample(lambda: Fake(), sleep=waits.append)
        self.assertEqual(result['power']['PSTR']['watts'], 4.0)   # Missing and failed reads are left out.
        self.assertEqual(waits, [t.POWER_SPACING_S] * (t.POWER_READS - 1))
        self.assertIsNone(t.sample(lambda: (_ for _ in ()).throw(OSError('denied')))['power']['PSTR']['watts'])

    def test_no_access_is_unavailable(self):
        def denied(): raise OSError('denied')
        self.assertTrue(all(s['temperature_c'] is None for s in t.sample(denied)['sensors'].values()))
        self.assertIsNone(t.sample(denied)['power']['PSTR']['watts'])

    def test_read_only_keys_and_commands(self):
        r = t.Reader.__new__(t.Reader)
        with self.assertRaises(ValueError): r.read('F0Tg')
        with self.assertRaises(ValueError): r._read(t.Data(command=6))
        self.assertEqual(t.C.sizeof(t.Data), 80)
