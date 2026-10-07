from __future__ import annotations

import asyncio
import json
import struct

import pytest

from grok_usage import (
    _read_grok_bot_usage,
    export_grok_quota,
    parse_grok_bot_usage,
    parse_grok_credits_response,
)


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            break
    return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _len_field(field: int, data: bytes) -> bytes:
    return _key(field, 2) + _varint(len(data)) + data


def _float_field(field: int, value: float) -> bytes:
    return _key(field, 5) + struct.pack('<f', value)


def _timestamp(seconds: int) -> bytes:
    return _key(1, 0) + _varint(seconds)


def _envelope(message: bytes) -> bytes:
    return b'\x00' + struct.pack('>I', len(message)) + message + b'\x80' + struct.pack('>I', 15) + b'grpc-status:0\r\n'


def test_parse_grok_credits_response_weekly_pool():
    # GrokCreditsConfig: percent=12.5, weekly period ending at known epoch
    period = (
        _key(1, 0)
        + _varint(2)  # WEEKLY
        + _len_field(2, _timestamp(1_785_224_849))
        + _len_field(3, _timestamp(1_785_829_649))
    )
    product = _key(1, 0) + _varint(2) + _float_field(2, 12.5)
    config = (
        _float_field(1, 12.5)
        + _len_field(7, product)
        + _len_field(8, period)
    )
    response = _len_field(1, config)
    body = _envelope(response)

    parsed = parse_grok_credits_response(body)
    assert parsed['used_percentage'] == 12
    assert parsed['period_label'] == 'Weekly'
    assert parsed['next_reset_time_ms'] == 1_785_829_649 * 1000
    assert parsed['product_usage'][0]['product'] == 2
    assert parsed['product_usage'][0]['usage_percent'] == 12.5


def test_export_preserves_product_code_without_inventing_name(monkeypatch):
    import grok_usage
    product = _key(1, 0) + _varint(2) + _float_field(2, 12.5)
    body = _envelope(_len_field(1, _float_field(1, 12.5) + _len_field(7, product)))
    monkeypatch.setattr(grok_usage, 'fetch_grok_credits_config', lambda cookie: body)
    snapshot = export_grok_quota('fixture-cookie')[0]
    assert snapshot['product_usage'] == [{'product': 2, 'label': 'Grok Build', 'usage_percent': 12.5}]
    assert 'tokens' not in snapshot


def test_parse_grok_credits_response_zero_usage_omits_percent_fields():
    # Live 0% responses omit proto3 default floats (field 1 and product_usage).
    period = (
        _key(1, 0)
        + _varint(2)  # WEEKLY
        + _len_field(2, _timestamp(1_785_829_649))
        + _len_field(3, _timestamp(1_786_434_449))
    )
    config = (
        _len_field(2, b'')
        + _len_field(3, b'')
        + _len_field(4, _timestamp(1_785_829_649))
        + _len_field(5, _timestamp(1_786_434_449))
        + _len_field(8, period)
        + _key(11, 0)
        + _varint(1)
        + _len_field(12, b'')
        + _key(13, 0)
        + _varint(1)
    )
    body = _envelope(_len_field(1, config))

    parsed = parse_grok_credits_response(body)
    assert parsed['credit_usage_percent'] == 0.0
    assert parsed['used_percentage'] == 0
    assert parsed['period_label'] == 'Weekly'
    assert parsed['next_reset_time_ms'] == 1_786_434_449 * 1000
    assert parsed['product_usage'] == []


def test_parse_grok_bot_usage_is_separate_and_offset_aware():
    quota = parse_grok_bot_usage({
        'jsonrpc': '2.0', 'id': 1,
        'result': {'usagePercent': 100.0, 'nextResetAtMs': 1_791_537_783_029},
    })
    assert quota == {
        'provider': 'grok_bot', 'label': 'Weekly Grok Bot Limit', 'percentage': 100,
        'next_reset_time_ms': 1_791_537_783_029,
        'next_reset_iso': '2026-10-09T09:23:03.029+00:00',
    }


@pytest.mark.parametrize('result', [
    {'usagePercent': None, 'nextResetAtMs': 1_791_537_783_029},
    {'usagePercent': float('nan'), 'nextResetAtMs': 1_791_537_783_029},
    {'usagePercent': 101, 'nextResetAtMs': 1_791_537_783_029},
    {'usagePercent': 0, 'nextResetAtMs': None},
])
def test_parse_grok_bot_usage_rejects_incomplete_results(result):
    with pytest.raises(ValueError):
        parse_grok_bot_usage({'jsonrpc': '2.0', 'id': 1, 'result': result})


def test_grok_bot_transport_sends_only_usage_read(monkeypatch):
    class Socket:
        def __init__(self):
            self.sent = []
            self.replies = [
                {'connection_id': 'fixture'},
                {'jsonrpc': '2.0', 'id': 1, 'result': {
                    'usagePercent': 42.5, 'nextResetAtMs': 1_791_537_783_029,
                }},
            ]

        async def send(self, value):
            self.sent.append(json.loads(value))

        async def recv(self):
            return json.dumps(self.replies.pop(0))

    class Connection:
        async def __aenter__(self):
            return socket

        async def __aexit__(self, *_):
            return False

    socket = Socket()
    connection = {}
    import websockets
    def connect(url, **kwargs):
        connection.update(url=url, **kwargs)
        return Connection()
    monkeypatch.setattr(websockets, 'connect', connect)
    quota = asyncio.run(_read_grok_bot_usage('fixture-cookie', 1))
    assert quota['provider'] == 'grok_bot'
    assert quota['percentage'] == 42
    assert connection['extra_headers']['Cookie'] == 'fixture-cookie'
    assert connection['origin'] == 'https://grok.com'
    assert socket.sent == [
        {'protocol_version': '1.0.0', 'kind': 'bot_client'},
        {'jsonrpc': '2.0', 'id': 1, 'method': 'bot.usage', 'params': {}},
    ]
