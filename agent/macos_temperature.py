"""Local-only TCMb/TCMz probe. No network, credentials, or monitor integration.

SMC read protocol reference: vladkens/macmon src_lib/sources.rs (MIT).
Labels follow OSHI; Apple M6 sensor mapping is not officially confirmed.
"""
from __future__ import annotations
import ctypes as C
import json
import math
import platform
import struct

KEYS = ('TCMb', 'TCMz')


class Version(C.Structure):
    _fields_ = [('major', C.c_uint8), ('minor', C.c_uint8), ('build', C.c_uint8),
                ('reserved', C.c_uint8), ('release', C.c_uint16)]


class Limits(C.Structure):
    _fields_ = [('version', C.c_uint16), ('length', C.c_uint16),
                ('cpu', C.c_uint32), ('gpu', C.c_uint32), ('memory', C.c_uint32)]


class Info(C.Structure):
    _fields_ = [('size', C.c_uint32), ('type', C.c_uint32), ('attributes', C.c_uint8)]


class Data(C.Structure):
    _fields_ = [('key', C.c_uint32), ('version', Version), ('limits', Limits),
                ('info', Info), ('result', C.c_uint8), ('status', C.c_uint8),
                ('command', C.c_uint8), ('index', C.c_uint32), ('bytes', C.c_uint8 * 32)]


def decode_temperature(kind, raw):
    if kind != b'flt ' or len(raw) != 4:
        return None
    value = struct.unpack('<f', raw)[0]
    # Exclude missing/zero and impossible readings; never fabricate a fallback max.
    return round(value, 2) if math.isfinite(value) and 0 < value <= 150 else None


class Reader:
    def __init__(self):
        if platform.system() != 'Darwin':
            raise OSError('macOS required')
        self.io = C.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
        self.conn = C.c_uint32()
        signatures = {
            'IOServiceMatching': ([C.c_char_p], C.c_void_p),
            'IOServiceGetMatchingServices': ([C.c_uint32, C.c_void_p, C.POINTER(C.c_uint32)], C.c_int),
            'IOIteratorNext': ([C.c_uint32], C.c_uint32),
            'IORegistryEntryGetName': ([C.c_uint32, C.c_char_p], C.c_int),
            'IOServiceOpen': ([C.c_uint32, C.c_uint32, C.c_uint32, C.POINTER(C.c_uint32)], C.c_int),
            'IOObjectRelease': ([C.c_uint32], C.c_int),
            'IOServiceClose': ([C.c_uint32], C.c_int),
            'IOConnectCallStructMethod': ([C.c_uint32, C.c_uint32, C.c_void_p, C.c_size_t,
                                          C.c_void_p, C.POINTER(C.c_size_t)], C.c_int),
        }
        for name, (args, result) in signatures.items():
            f = getattr(self.io, name); f.argtypes = args; f.restype = result
        task = C.c_uint32.in_dll(C.CDLL('/usr/lib/libSystem.B.dylib'), 'mach_task_self_').value
        iterator = C.c_uint32()
        if self.io.IOServiceGetMatchingServices(0, self.io.IOServiceMatching(b'AppleSMC'), C.byref(iterator)):
            raise OSError('SMC unavailable')
        try:
            while device := self.io.IOIteratorNext(iterator):
                try:
                    name = C.create_string_buffer(128)
                    if self.io.IORegistryEntryGetName(device, name) == 0 and name.value == b'AppleSMCKeysEndpoint':
                        if self.io.IOServiceOpen(device, task, 0, C.byref(self.conn)):
                            raise OSError('SMC access denied')
                        break
                finally:
                    self.io.IOObjectRelease(device)
        finally:
            self.io.IOObjectRelease(iterator)
        if not self.conn.value:
            raise OSError('SMC endpoint unavailable')

    def close(self):
        if self.conn.value:
            self.io.IOServiceClose(self.conn)
            self.conn.value = 0

    def _read(self, request):
        # Only metadata/read commands. No write, fan or power controls.
        if request.command not in (9, 5):
            raise ValueError('read only')
        output = Data(); size = C.c_size_t(C.sizeof(output))
        result = self.io.IOConnectCallStructMethod(self.conn, 2, C.byref(request),
                    C.sizeof(request), C.byref(output), C.byref(size))
        if result or output.result or size.value != C.sizeof(output):
            raise OSError('SMC key unavailable')
        return output

    def read(self, key):
        if key not in KEYS:
            raise ValueError('unsupported key')
        request = Data(key=int.from_bytes(key.encode('ascii'), 'big'), command=9)
        info = self._read(request).info
        if info.size != 4 or info.type != int.from_bytes(b'flt ', 'big'):
            return None
        request.command = 5; request.info = info
        return decode_temperature(b'flt ', bytes(self._read(request).bytes[:4]))


def sample(factory=Reader):
    readings = {key: None for key in KEYS}
    try:
        reader = factory()
    except OSError:
        reader = None
    if reader is not None:
        try:
            for key in KEYS:
                try:
                    readings[key] = reader.read(key)
                except OSError:
                    pass
        finally:
            reader.close()
    return {'source': 'AppleSMC', 'mapping': 'OSHI CPU die average/max; M6 mapping not officially confirmed',
            'sensors': {key: {'label': 'CPU die average' if key == 'TCMb' else 'CPU die maximum',
                              'temperature_c': readings[key]} for key in KEYS}}


if __name__ == '__main__':
    print(json.dumps(sample(), allow_nan=False))
