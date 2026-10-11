import os
import sys
import struct
import zlib
import hashlib
import unittest
import socket
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.nicotine_integration import (
    SoulseekProtocol, P2PManager, PeerServer,
    _pack_string, _unpack_string, _send_message, _unpack_bool,
    _send_init_message, _pack_peer_init, _parse_peer_init,
    CODE_LOGIN, CODE_LOGIN_RESP, CODE_FILE_SEARCH,
    CODE_INIT_PEER_INIT, CODE_INIT_PIERCE_FIREWALL,
    CODE_P_SEARCH_RESPONSE, CODE_P_QUEUE_UPLOAD,
    CODE_P_TRANSFER_REQUEST, CODE_P_TRANSFER_RESPONSE,
    CODE_CONNECT_TO_PEER, CODE_GET_PEER_ADDRESS,
    CODE_CONNECT_TO_PEER_RESP, CODE_GET_PEER_ADDRESS_RESP,
    CODE_EXCLUDED_SEARCH_PHRASES,
    CLIENT_MAJOR_VERSION, CLIENT_MINOR_VERSION,
    DEFAULT_SERVER, DEFAULT_PORT,
)


class TestPackUnpack(unittest.TestCase):
    def test_pack_string_uses_length_prefix(self):
        data = _pack_string('hello')
        length = struct.unpack('<I', data[:4])[0]
        self.assertEqual(length, 5)
        self.assertEqual(data[4:], b'hello')

    def test_pack_string_encodes_unicode(self):
        data = _pack_string('héllo')
        length = struct.unpack('<I', data[:4])[0]
        self.assertEqual(length, 6)
        self.assertEqual(data[4:], 'héllo'.encode())

    def test_unpack_string_reads_length_prefix(self):
        buf = b'\x05\x00\x00\x00hello\x05\x00\x00\x00world'
        s, offset = _unpack_string(buf, 0)
        self.assertEqual(s, 'hello')
        self.assertEqual(offset, 9)

    def test_unpack_string_reads_rest_on_truncated(self):
        buf = b'\x0b\x00\x00\x00hello world'
        s, offset = _unpack_string(buf, 0)
        self.assertEqual(s, 'hello world')
        self.assertEqual(offset, len(buf))

    def test_unpack_string_with_offset(self):
        buf = b'xyz\x08\x00\x00\x00testuserxyz'
        s, offset = _unpack_string(buf, 3)
        self.assertEqual(s, 'testuser')
        self.assertEqual(offset, 15)


class TestSendMessage(unittest.TestCase):
    def test_send_message_packs_correctly(self):
        mock_sock = MagicMock()
        payload = b'test'
        _send_message(mock_sock, CODE_LOGIN, payload)
        msg = struct.pack('<I', CODE_LOGIN) + payload
        header = struct.pack('<I', len(msg))
        mock_sock.sendall.assert_called_once_with(header + msg)

    def test_send_message_empty_payload(self):
        mock_sock = MagicMock()
        _send_message(mock_sock, CODE_LOGIN)
        msg = struct.pack('<I', CODE_LOGIN) + b''
        header = struct.pack('<I', len(msg))
        mock_sock.sendall.assert_called_once_with(header + msg)


class TestSoulseekProtocol(unittest.TestCase):
    def setUp(self):
        self.protocol = SoulseekProtocol()

    def test_initial_state(self):
        self.assertEqual(self.protocol.server, DEFAULT_SERVER)
        self.assertEqual(self.protocol.port, DEFAULT_PORT)
        self.assertFalse(self.protocol.connected)
        self.assertIsNone(self.protocol._socket)
        self.assertEqual(self.protocol.username, '')
        self.assertEqual(self.protocol.password, '')

    def test_connect_returns_true_and_starts_thread(self):
        with patch('core.nicotine_integration.socket.socket'), \
             patch.object(self.protocol, '_connect_async') as mock_async:
            result = self.protocol.connect('user', 'pass')
            self.assertTrue(result)
            mock_async.assert_called_once()

    def test_is_connected_property(self):
        self.assertFalse(self.protocol.connected)
        self.protocol.connected = True
        self.assertTrue(self.protocol.connected)

    def test_disconnect_sets_stop_event(self):
        with patch.object(self.protocol, '_cleanup') as mock_cleanup:
            self.protocol.disconnect()
            self.assertTrue(self.protocol._stop_event.is_set())
            mock_cleanup.assert_called_once_with("User requested disconnect")

    def test_send_login_packets_correctly(self):
        mock_sock = MagicMock()
        self.protocol.username = 'testuser'
        self.protocol.password = 'testpass'
        self.protocol._send_login(mock_sock)
        md5_hash = hashlib.md5(b'testusertestpass').hexdigest()
        expected_payload = (
            _pack_string('testuser') +
            _pack_string('testpass') +
            struct.pack('<I', CLIENT_MAJOR_VERSION) +
            _pack_string(md5_hash) +
            struct.pack('<I', CLIENT_MINOR_VERSION)
        )
        expected_msg = struct.pack('<I', CODE_LOGIN) + expected_payload
        expected_header = struct.pack('<I', len(expected_msg))
        mock_sock.sendall.assert_called_once_with(expected_header + expected_msg)

    def test_search_returns_ticket_when_connected(self):
        mock_sock = MagicMock()
        self.protocol._socket = mock_sock
        self.protocol.connected = True
        ticket = self.protocol.search('test query')
        self.assertIsInstance(ticket, int)
        data = mock_sock.sendall.call_args[0][0]
        length = struct.unpack('<I', data[:4])[0]
        body = data[4:4 + length]
        self.assertEqual(struct.unpack('<I', body[:4])[0], CODE_FILE_SEARCH)
        sent_token = struct.unpack('<I', body[4:8])[0]
        self.assertEqual(sent_token, ticket)
        query, _ = _unpack_string(body, 8)
        self.assertEqual(query, 'test query')

    def test_search_returns_empty_when_disconnected(self):
        self.protocol.connected = False
        self.protocol._socket = None
        result = self.protocol.search('test')
        self.assertEqual(result, [])

    def test_unpack_bool_reads_single_byte(self):
        self.assertEqual(_unpack_bool(b'\x01', 0), (True, 1))
        self.assertEqual(_unpack_bool(b'\x00', 0), (False, 1))

    def test_handle_login_resp_success(self):
        greet = _pack_string('Welcome')
        payload = b'\x01' + greet + b'\x7f\x00\x00\x01' + _pack_string('abc') + b'\x00'
        self.protocol._login_event = __import__('threading').Event()
        with patch.object(self.protocol, '_send_post_login'):
            self.protocol._handle_login_resp(payload)
        self.assertTrue(self.protocol._login_success)

    def test_handle_login_resp_failure(self):
        reason = _pack_string('INVALIDPASS')
        payload = b'\x00' + reason
        self.protocol._login_event = __import__('threading').Event()
        with patch.object(self.protocol, 'disconnect'):
            self.protocol._handle_login_resp(payload)
        self.assertFalse(self.protocol._login_success)

    def test_download_returns_none_when_disconnected(self):
        self.protocol.connected = False
        result = self.protocol.download('user', 'file.mp3')
        self.assertIsNone(result)

    def test_download_returns_ticket_when_connected(self):
        mock_sock = MagicMock()
        self.protocol._socket = mock_sock
        self.protocol.connected = True
        result = self.protocol.download('user', 'file.mp3')
        self.assertIsNotNone(result)


class TestPeerInitFraming(unittest.TestCase):
    """Peer init frames carry a uint8 code, unlike uint32 server/peer codes."""

    def test_init_message_uses_uint8_code(self):
        sock = MagicMock()
        _send_init_message(sock, CODE_INIT_PEER_INIT, b'abc')
        expected = struct.pack('<B', CODE_INIT_PEER_INIT) + b'abc'
        sock.sendall.assert_called_once_with(struct.pack('<I', len(expected)) + expected)

    def test_init_length_covers_code_and_payload(self):
        sock = MagicMock()
        _send_init_message(sock, CODE_INIT_PIERCE_FIREWALL, struct.pack('<I', 7))
        data = sock.sendall.call_args[0][0]
        declared = struct.unpack('<I', data[:4])[0]
        self.assertEqual(declared, len(data) - 4)
        self.assertEqual(data[4], 0)  # uint8 code
        self.assertEqual(struct.unpack('<I', data[5:9])[0], 7)

    def test_server_message_uses_uint32_code(self):
        sock = MagicMock()
        _send_message(sock, CODE_LOGIN, b'x')
        data = sock.sendall.call_args[0][0]
        declared = struct.unpack('<I', data[:4])[0]
        self.assertEqual(declared, len(data) - 4)
        self.assertEqual(struct.unpack('<I', data[4:8])[0], CODE_LOGIN)

    def test_pack_parse_peer_init_roundtrip(self):
        payload = _pack_peer_init('myuser', 'P')
        username, conn_type = _parse_peer_init(payload)
        self.assertEqual(username, 'myuser')
        self.assertEqual(conn_type, 'P')

    def test_peer_init_token_is_zero(self):
        payload = _pack_peer_init('u', 'P')
        self.assertEqual(struct.unpack('<I', payload[-4:])[0], 0)


def _build_search_response(username, token, files):
    """Build a FileSearchResponse payload exactly as peers transmit it."""
    msg = bytearray()
    msg += _pack_string(username)
    msg += struct.pack('<I', token)
    msg += struct.pack('<I', len(files))
    for filename, size, ext, attrs in files:
        msg += struct.pack('<B', 1)
        msg += _pack_string(filename)
        msg += struct.pack('<Q', size)
        msg += _pack_string(ext)
        msg += struct.pack('<I', len(attrs))
        for atype, aval in attrs:
            msg += struct.pack('<I', atype)
            msg += struct.pack('<I', aval)
    return zlib.compress(bytes(msg))


class TestSearchResponseParsing(unittest.TestCase):
    """FileSearchResponse is zlib compressed and starts with a username string."""

    def setUp(self):
        self.ps = PeerServer('me')
        self.received = []
        self.ps.on_peer_search_response = self.received.append

    def test_parses_compressed_payload(self):
        payload = _build_search_response('peer1', 4242, [
            ('Music/song.mp3', 5242880, 'mp3', [(0, 320), (1, 210)]),
        ])
        self.ps._parse_search_results(payload, 'peer1')
        self.assertEqual(len(self.received), 1)
        result = self.received[0][0]
        self.assertEqual(result['filename'], 'Music/song.mp3')
        self.assertEqual(result['size'], 5242880)
        self.assertEqual(result['extension'], 'mp3')
        self.assertEqual(result['bitrate'], 320)
        self.assertEqual(result['length'], 210)
        self.assertEqual(result['user'], 'peer1')

    def test_parses_multiple_results(self):
        payload = _build_search_response('peer1', 1, [
            ('a.mp3', 100, 'mp3', [(1, 60)]),
            ('b.flac', 200, 'flac', [(1, 120), (4, 44100), (5, 16)]),
        ])
        self.ps._parse_search_results(payload, 'peer1')
        self.assertEqual(len(self.received[0]), 2)
        self.assertEqual(self.received[0][1]['extension'], 'flac')

    def test_ignores_uncompressed_payload(self):
        # A payload that was never compressed must not raise.
        self.ps._parse_search_results(b'not compressed at all', 'peer1')
        self.assertEqual(self.received, [])

    def test_handles_empty_result_list(self):
        self.ps._parse_search_results(_build_search_response('p', 1, []), 'p')
        self.assertEqual(self.received, [])

    def test_handles_truncated_compressed_payload(self):
        payload = _build_search_response('p', 1, [('a.mp3', 100, 'mp3', [(1, 60)])])
        self.ps._parse_search_results(payload[:len(payload) // 2], 'p')
        # Must not raise; may or may not yield results.
        self.assertIsInstance(self.received, list)


class TestDownloadRequestFlow(unittest.TestCase):
    def setUp(self):
        self.protocol = SoulseekProtocol()

    def test_download_sends_connect_to_peer_then_get_peer_address(self):
        sock = MagicMock()
        self.protocol._socket = sock
        self.protocol.connected = True
        ticket = self.protocol.download('peer1', 'a/b.mp3', '/tmp/b.mp3')
        self.assertIsNotNone(ticket)
        codes = []
        for call_obj in sock.sendall.call_args_list:
            data = call_obj[0][0]
            length = struct.unpack('<I', data[:4])[0]
            body = data[4:4 + length]
            codes.append(struct.unpack('<I', body[:4])[0])
        self.assertEqual(codes, [CODE_CONNECT_TO_PEER, CODE_GET_PEER_ADDRESS])

    def test_connect_to_peer_payload_has_token_username_type(self):
        sock = MagicMock()
        self.protocol._socket = sock
        self.protocol.connected = True
        ticket = self.protocol.download('peer1', 'a/b.mp3', '/tmp/b.mp3')
        data = sock.sendall.call_args_list[0][0][0]
        length = struct.unpack('<I', data[:4])[0]
        body = data[4:4 + length]
        payload = body[4:]
        token = struct.unpack('<I', payload[:4])[0]
        username, offset = _unpack_string(payload, 4)
        conn_type, _ = _unpack_string(payload, offset)
        self.assertEqual(token, ticket)
        self.assertEqual(username, 'peer1')
        self.assertEqual(conn_type, 'P')

    def test_tickets_are_unique_across_rapid_calls(self):
        sock = MagicMock()
        self.protocol._socket = sock
        self.protocol.connected = True
        tickets = {self.protocol.download('p', f'{i}.mp3', f'/tmp/{i}.mp3')
                   for i in range(20)}
        self.assertEqual(len(tickets), 20)

    def test_failed_send_clears_pending_entry(self):
        sock = MagicMock()
        sock.sendall.side_effect = OSError("broken pipe")
        self.protocol._socket = sock
        self.protocol.connected = True
        self.assertIsNone(self.protocol.download('p', 'a.mp3', '/tmp/a.mp3'))
        self.assertEqual(self.protocol._pending_downloads, {})

    def test_transfer_request_records_expected_size(self):
        self.protocol._pending_downloads[7] = {
            "peer_user": "p", "filename": "a.mp3",
            "output_path": "/tmp/a.mp3", "ticket": 7, "size": 0, "queued": False,
        }
        payload = struct.pack('<I', 1) + struct.pack('<I', 99)
        payload += _pack_string('a.mp3') + struct.pack('<Q', 123456)
        self.protocol._handle_peer_transfer_request(7, payload)
        self.assertEqual(self.protocol._pending_downloads[7]['size'], 123456)

    def test_transfer_request_rejects_truncated_payload(self):
        errors = []
        self.protocol.on_error = errors.append
        self.assertIsNone(
            self.protocol._handle_peer_transfer_request(7, b'\x01\x00'))


class TestServerMessageHandling(unittest.TestCase):
    def setUp(self):
        self.protocol = SoulseekProtocol()

    def test_excluded_search_phrases_parsed(self):
        payload = struct.pack('<I', 2) + _pack_string('bad') + _pack_string('worse')
        self.protocol._handle_message(160, payload)
        self.assertEqual(self.protocol.excluded_phrases, ['bad', 'worse'])

    def test_excluded_search_phrases_ignores_garbage(self):
        self.protocol._handle_message(160, b'')
        self.assertEqual(self.protocol.excluded_phrases, [])

    def test_pierce_firewall_spawns_connection(self):
        payload = _pack_string('peer1') + _pack_string('P')
        payload += socket.inet_aton('1.2.3.4')[::-1]
        payload += struct.pack('<I', 1234) + struct.pack('<I', 99)
        with patch.object(self.protocol, '_pierce_firewall') as pierce:
            self.protocol._handle_message(CODE_CONNECT_TO_PEER_RESP, payload)
        pierce.assert_called_once()
        self.assertEqual(pierce.call_args[0], ('1.2.3.4', 1234, 'peer1', 'P', 99))

    def test_peer_address_response_ignores_zero_port(self):
        errors = []
        self.protocol.on_error = errors.append
        payload = _pack_string('peer1') + socket.inet_aton('1.2.3.4')[::-1]
        payload += struct.pack('<I', 0)
        self.protocol._handle_message(CODE_GET_PEER_ADDRESS_RESP, payload)
        self.assertTrue(any('offline' in e for e in errors))

    def test_unknown_message_does_not_raise(self):
        self.protocol._handle_message(99999, b'\x00\x00')


class TestConnectToPeerResponse(unittest.TestCase):
    def test_sends_init_code_zero(self):
        protocol = SoulseekProtocol()
        sock = MagicMock()
        with patch('core.nicotine_integration.socket.socket') as mock_socket:
            mock_socket.return_value = sock
            protocol._pierce_firewall('1.2.3.4', 1234, 'peer', 'P', 555)
        data = sock.sendall.call_args[0][0]
        length = struct.unpack('<I', data[:4])[0]
        self.assertEqual(data[4], CODE_INIT_PIERCE_FIREWALL)
        self.assertEqual(struct.unpack('<I', data[5:9])[0], 555)
        self.assertEqual(length, 5)


class TestP2PManager(unittest.TestCase):
    def setUp(self):
        self.manager = P2PManager()

    def test_initial_state(self):
        self.assertIsNotNone(self.manager.protocol)
        self.assertFalse(self.manager.is_connected)
        self.assertEqual(self.manager.search_results, [])
        self.assertEqual(self.manager.downloads, [])

    def test_is_connected_delegates_to_protocol(self):
        self.assertFalse(self.manager.is_connected)
        self.manager.protocol.connected = True
        self.assertTrue(self.manager.is_connected)

    def test_connect_returns_true(self):
        with patch.object(self.manager.protocol, 'connect') as mock_connect:
            result = self.manager.connect('user', 'pass')
            self.assertTrue(result)
            mock_connect.assert_called_once_with('user', 'pass', DEFAULT_SERVER, DEFAULT_PORT)

    def test_disconnect_calls_protocol(self):
        with patch.object(self.manager.protocol, 'disconnect') as mock_dc:
            self.manager.disconnect()
            mock_dc.assert_called_once()

    def test_search_files_returns_none_when_disconnected(self):
        result = self.manager.search_files('test')
        self.assertIsNone(result)

    def test_search_files_returns_ticket_when_connected(self):
        self.manager.protocol.connected = True
        with patch.object(self.manager.protocol, 'search', return_value=12345):
            ticket = self.manager.search_files('test')
            self.assertEqual(ticket, 12345)

    def test_download_file_returns_none_when_disconnected(self):
        result = self.manager.download_file('user', 'file.mp3')
        self.assertIsNone(result)

    def test_download_file_appends_to_downloads_when_connected(self):
        self.manager.protocol.connected = True
        with patch.object(self.manager.protocol, 'download', return_value=12345):
            result = self.manager.download_file('user', 'song.mp3')
            self.assertEqual(result, 12345)
            self.assertEqual(len(self.manager.downloads), 1)
            self.assertEqual(self.manager.downloads[0]['user'], 'user')
            self.assertEqual(self.manager.downloads[0]['status'], 'queued')

    def test_search_files_returns_none_and_logs_when_disconnected(self):
        calls = []
        manager = P2PManager(status_callback=calls.append)
        result = manager.search_files('test')
        self.assertIsNone(result)
        self.assertIn('Not connected to Soulseek', calls)


if __name__ == '__main__':
    unittest.main()
