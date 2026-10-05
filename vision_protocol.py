"""vision.v1: bounded JSON control only. No media, credentials or arbitrary proxy.

This pure-stdlib file is intentionally byte-identical to floor-presence's
vision/control_protocol.py. Unix deadlines require synchronized deployment clocks.
"""
import copy
import hashlib
import json
import math
import re

PROTOCOL = 'vision.v1'
MAX_BYTES = 32768
MAX_DEADLINE = 30.0
ACTIONS = frozenset(('status.get', 'config.get', 'config.replace', 'detector.configure'))
SCOPES = {'status.get': 'status', 'config.get': 'status', 'config.replace': 'edit', 'detector.configure': 'edit'}
CODES = frozenset(('adapter_unavailable', 'invalid_payload', 'revision_conflict', 'session_invalid', 'deadline_exceeded', 'duplicate_conflict', 'execution_unknown', 'capacity_exceeded', 'device_unavailable', 'credential_revoked', 'protocol_invalid'))
IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
NONCE = re.compile(r'^[A-Za-z0-9_-]{22,128}$')
PRIVATE_TEXT = re.compile(r'(?:https?|rtsp|wss?|ftp|file|data):|\b(?:\d{1,3}\.){3}\d{1,3}\b', re.I)


class ProtocolError(ValueError):
    def __init__(self, code='invalid_payload'):
        self.code = code
        super().__init__(code)  # Never echo the supplied value.


def _fail():
    raise ProtocolError()


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def decode_message(raw):
    try:
        if isinstance(raw, dict):
            encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        elif isinstance(raw, str):
            encoded = raw.encode('utf-8')
        elif isinstance(raw, bytes):
            encoded = raw
        else:
            _fail()
        if len(encoded) > MAX_BYTES:
            _fail()
        value = json.loads(encoded, object_pairs_hook=_object_pairs, parse_constant=lambda _: _fail())
        if not isinstance(value, dict):
            _fail()
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProtocolError() from None


def encode_message(value):
    return json.dumps(decode_message(value), ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _keys(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        _fail()


def _number(value):
    return type(value) is int or type(value) is float and math.isfinite(value)


def _revision(value):
    if type(value) is not int or not 0 <= value <= 2**53-1:
        _fail()


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        _fail()


def _text(value, maximum=100):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or PRIVATE_TEXT.search(value) or any(ord(ch) < 32 for ch in value):
        _fail()


def _detector(value, revision_key='revision'):
    _keys(value, (revision_key, 'model', 'precision'))
    _revision(value[revision_key])
    if value['model'] not in ('yolo11n', 'yolo11s') or value['precision'] not in ('fp16', 'fp32'):
        _fail()


def validate_floor_config(c):
    """Shape/size guard; floor service performs full geometry/domain validation."""
    _keys(c, ('version', 'geometry_id', 'source', 'zones'))
    if type(c['version']) is not int or c['version'] not in (1, 2):
        _fail()
    _text(c['geometry_id'])
    _keys(c['source'], ('kind', 'stale_after'))
    if c['source']['kind'] not in ('simulation', 'video', 'rtsp') or not _number(c['source']['stale_after']) or not .1 <= c['source']['stale_after'] <= 60:
        _fail()
    if not isinstance(c['zones'], list) or len(c['zones']) > 32:
        _fail()
    for zone in c['zones']:
        polygon_key = 'polygons' if c['version'] == 2 else 'polygon'
        required = {'id', 'name', polygon_key, 'enter_delay', 'exit_delay', 'boundary_margin', 'occlusion_timeout'}
        if not isinstance(zone, dict) or not required <= set(zone) or set(zone) - required - {'grounding', 'visibility'}:
            _fail()
        _id(zone['id']); _text(zone['name'], 80)
        if zone.get('grounding', 'floor') not in ('floor', 'seat', 'semantic') or zone.get('visibility', 'visible') not in ('visible', 'obstructed'):
            _fail()
        for field, upper in [('enter_delay', 30), ('exit_delay', 60), ('boundary_margin', .1), ('occlusion_timeout', 30)]:
            if not _number(zone[field]) or not 0 <= zone[field] <= upper:
                _fail()
        polygons = zone[polygon_key] if c['version'] == 2 else [zone[polygon_key]]
        if not isinstance(polygons, list) or not 1 <= len(polygons) <= 16:
            _fail()
        total = 0
        for polygon in polygons:
            if not isinstance(polygon, list) or not 3 <= len(polygon) <= 64:
                _fail()
            total += len(polygon)
            for point in polygon:
                if not isinstance(point, list) or len(point) != 2 or any(not _number(x) or not 0 <= x <= 1 for x in point):
                    _fail()
        if total > 256:
            _fail()
    return c


def validate_payload(action, payload):
    if action in ('status.get', 'config.get'):
        _keys(payload, ())
    elif action == 'config.replace':
        _keys(payload, ('expected_revision', 'config'))
        _revision(payload['expected_revision'])
        validate_floor_config(payload['config'])
    elif action == 'detector.configure':
        _detector(payload, 'expected_revision')
    else:
        _fail()
    return payload


def _identity(value, message_type):
    if value.get('protocol') != PROTOCOL or value.get('type') != message_type:
        _fail()
    _id(value.get('device_id'))
    _id(value.get('request_id'))
    if message_type != 'command' and (not isinstance(value.get('session_nonce'), str) or not NONCE.fullmatch(value['session_nonce'])):
        _fail()


def validate_request(raw, now, expected_device=None, expected_session=None, allow_expired=False):
    value = decode_message(raw)
    _keys(value, ('protocol', 'type', 'request_id', 'session_nonce', 'device_id', 'action', 'deadline', 'payload'))
    _identity(value, 'request')
    if expected_device is not None and value['device_id'] != expected_device or expected_session is not None and value['session_nonce'] != expected_session:
        raise ProtocolError('session_invalid')
    _deadline(value, now, allow_expired)
    validate_payload(value['action'], value['payload'])
    return value


def validate_command(raw, now, allow_expired=False):
    value = decode_message(raw)
    _keys(value, ('protocol', 'type', 'request_id', 'device_id', 'action', 'deadline', 'payload'))
    _identity(value, 'command')
    _deadline(value, now, allow_expired)
    validate_payload(value['action'], value['payload'])
    return value


def _deadline(value, now, allow_expired):
    if not _number(now) or not _number(value['deadline']) or value['deadline'] > now + MAX_DEADLINE:
        _fail()
    if not allow_expired and value['deadline'] <= now:
        raise ProtocolError('deadline_exceeded')


def request_fingerprint(request):
    fields = {k: v for k, v in request.items() if k != 'request_id'}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def make_result(request, status, payload=None, code=None):
    result = {k: request[k] for k in ('protocol', 'request_id', 'session_nonce', 'device_id')}
    result.update(type='result', status=status)
    if status == 'ok':
        result['payload'] = copy.deepcopy(payload)
    else:
        result['code'] = code or 'execution_unknown'
    return result


def validate_result(raw, expected_action=None):
    value = decode_message(raw)
    base = ('protocol', 'type', 'request_id', 'session_nonce', 'device_id', 'status')
    _identity(value, 'result')
    if value.get('status') in ('error', 'unknown'):
        _keys(value, (*base, 'code'))
        if not isinstance(value['code'], str) or value['code'] not in CODES:
            _fail()
        return value
    if value.get('status') != 'ok':
        _fail()
    _keys(value, (*base, 'payload'))
    payload = value['payload']
    action = expected_action
    if action is None and isinstance(payload, dict):
        action = 'status.get' if 'adapter' in payload else 'config.get' if 'config' in payload else 'detector.configure'
    if action == 'status.get':
        _keys(payload, ('adapter', 'available', 'config_revision', 'detector_revision', 'detector', 'zone_count'))
        if payload['adapter'] != 'synthetic' or type(payload['available']) is not bool or type(payload['zone_count']) is not int or not 0 <= payload['zone_count'] <= 32:
            _fail()
        _revision(payload['config_revision']); _revision(payload['detector_revision'])
        _keys(payload['detector'], ('model', 'precision'))
        _detector({'revision': 0, **payload['detector']})
    elif action in ('config.get', 'config.replace'):
        _keys(payload, ('revision', 'config'))
        _revision(payload['revision']); validate_floor_config(payload['config'])
    elif action == 'detector.configure':
        _detector(payload)
    else:
        _fail()
    return value


def validate_hello(raw):
    value = decode_message(raw)
    _keys(value, ('protocol', 'type', 'device_id'))
    if value['protocol'] != PROTOCOL or value['type'] != 'hello':
        _fail()
    _id(value['device_id'])
    return value
