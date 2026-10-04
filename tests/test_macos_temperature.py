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
        result = t.sample(lambda: reader)
        self.assertEqual(result['sensors']['TCMb']['temperature_c'], 44.5)
        self.assertIsNone(result['sensors']['TCMz']['temperature_c'])
        self.assertTrue(reader.closed)

    def test_no_access_is_unavailable(self):
        def denied(): raise OSError('denied')
        self.assertTrue(all(s['temperature_c'] is None for s in t.sample(denied)['sensors'].values()))

    def test_read_only_keys_and_commands(self):
        r = t.Reader.__new__(t.Reader)
        with self.assertRaises(ValueError): r.read('F0Tg')
        with self.assertRaises(ValueError): r._read(t.Data(command=6))
        self.assertEqual(t.C.sizeof(t.Data), 80)
