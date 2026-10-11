"""
NexusAudio - Integrated Soulseek P2P functionality
Implements the SLSK wire protocol over TCP sockets.

Message framing follows the Nicotine+ protocol reference (Aug 2026):

* Server messages: uint32 length, uint32 code, payload
* Peer messages:  uint32 length, uint32 code, payload
* Peer init:       uint32 length (code + payload), uint8 code, payload
* File messages:   raw, no code/length framing

Two details that are easy to get wrong and were previously wrong here:

* Peer init codes are **uint8**, while peer message codes are **uint32**.
* ``FileSearchResponse`` (peer code 9) payloads are **zlib compressed** and
  begin with a length-prefixed ``username`` string, and every file entry is
  preceded by a uint8 code byte.
"""
import os
import socket
import struct
import threading
import time
import zlib
import hashlib
import logging

logger = logging.getLogger(__name__)

# Message codes (Client → Server)
CODE_LOGIN = 1
CODE_SET_LISTEN_PORT = 2
CODE_GET_PEER_ADDRESS = 3
CODE_CONNECT_TO_PEER = 18
CODE_FILE_SEARCH = 26
CODE_SET_STATUS = 28
CODE_PING = 32
CODE_SHARED_FOLDERS = 35
CODE_HAVE_NO_PARENT = 71
CODE_CANT_CONNECT_TO_PEER = 1001

# Message codes (Server → Client)
CODE_LOGIN_RESP = 1
CODE_GET_PEER_ADDRESS_RESP = 3
CODE_GET_USER_STATUS = 7
CODE_GET_USER_STATS = 36
CODE_RELOGGED = 41
CODE_PLACE_IN_QUEUE_RESP = 44
CODE_SERVER_MESSAGE = 53
CODE_WISHLIST_INTERVAL = 104
CODE_CONNECT_TO_PEER_RESP = 18
CODE_EXCLUDED_SEARCH_PHRASES = 160

# Peer init codes (uint8)
CODE_INIT_PIERCE_FIREWALL = 0
CODE_INIT_PEER_INIT = 1

# Peer message codes (uint32)
CODE_P_SEARCH_RESPONSE = 9
CODE_P_TRANSFER_REQUEST = 40
CODE_P_TRANSFER_RESPONSE = 41
CODE_P_QUEUE_UPLOAD = 43
CODE_P_PLACE_IN_QUEUE_RESPONSE = 44
CODE_P_UPLOAD_FAILED = 46
CODE_P_UPLOAD_DENIED = 50
CODE_P_PLACE_IN_QUEUE_REQUEST = 51

# Connection types
CONN_TYPE_PEER = "P"
CONN_TYPE_FILE = "F"

# Transfer direction
TRANSFER_DIRECTION_DOWNLOAD = 0
TRANSFER_DIRECTION_UPLOAD = 1

# Client version — 177 is reserved for experimental/third-party clients and
# the server permits any minor version for them.
CLIENT_MAJOR_VERSION = 177
CLIENT_MINOR_VERSION = 1

DEFAULT_SERVER = "server.slsknet.org"
DEFAULT_PORT = 2242
SOCKET_TIMEOUT = 5
LOGIN_TIMEOUT = 15
MAX_RECONNECT_ATTEMPTS = 3
RECONNECT_BACKOFF_BASE = 2
TRANSFER_TIMEOUT = 60
CHUNK_SIZE = 65536
KEEPALIVE_IDLE = 10
KEEPALIVE_INTERVAL = 2
MAX_SEARCH_RESULTS = 10000
#: Guard against decompression bombs from a hostile peer.
MAX_DECOMPRESSED_SIZE = 128 * 1024 * 1024


def _pack_string(s):
    """Length-prefixed string: 4-byte LE length + UTF-8 bytes."""
    data = s.encode("utf-8", errors="replace")
    return struct.pack("<I", len(data)) + data


def _unpack_string(buf, offset):
    """Read a length-prefixed string from buffer at offset."""
    if offset + 4 > len(buf):
        return "", len(buf)
    length = struct.unpack("<I", buf[offset:offset + 4])[0]
    start = offset + 4
    end = start + length
    if end > len(buf):
        raw = buf[start:]
        return raw.decode("utf-8", errors="replace"), len(buf)
    raw = buf[start:end]
    return raw.decode("utf-8", errors="replace"), end


def _unpack_bool(buf, offset):
    if offset >= len(buf):
        return False, offset
    return bool(buf[offset]), offset + 1


def _set_socket_keepalive(sock, idle=KEEPALIVE_IDLE, interval=KEEPALIVE_INTERVAL):
    """Use TCP keepalive instead of ServerPing (code 32).

    The server no longer replies to ServerPing, so clients that rely on it for
    liveness drift into a disconnected state.
    """
    if hasattr(socket, "SO_KEEPALIVE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    if hasattr(socket, "TCP_KEEPIDLE"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, idle)
    if hasattr(socket, "TCP_KEEPINTVL"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval)
    if hasattr(socket, "TCP_KEEPCNT"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    elif hasattr(socket, "TCP_KEEPALIVE"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, idle)


def _read_exactly(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed")
        buf.extend(chunk)
    return bytes(buf)


def _send_message(sock, code, payload=b""):
    """Send a server or peer message (uint32 code)."""
    msg = struct.pack("<I", code) + payload
    sock.sendall(struct.pack("<I", len(msg)) + msg)


def _send_init_message(sock, code, payload=b""):
    """Send a peer init message.

    The length prefix covers the uint8 code plus the payload, unlike server
    and peer messages where the length covers the uint32 code plus payload.
    """
    msg = struct.pack("<B", code) + payload
    sock.sendall(struct.pack("<I", len(msg)) + msg)


def _recv_message(sock):
    """Read one uint32-coded message; returns (code, payload)."""
    raw_len = _read_exactly(sock, 4)
    msg_len = struct.unpack("<I", raw_len)[0]
    if msg_len < 4:
        raise ConnectionError("Invalid message length")
    msg = _read_exactly(sock, msg_len)
    return struct.unpack("<I", msg[:4])[0], msg[4:]


def _recv_init_message(sock):
    """Read one peer init message; returns (code, payload). Code is uint8."""
    raw_len = _read_exactly(sock, 4)
    msg_len = struct.unpack("<I", raw_len)[0]
    if msg_len < 1:
        raise ConnectionError("Invalid init message length")
    msg = _read_exactly(sock, msg_len)
    return msg[0], msg[1:]


def _pack_peer_init(username, conn_type):
    """Build a PeerInit (init code 1) payload.

    The token is always zero and ignored by current clients.
    """
    return _pack_string(username) + _pack_string(conn_type) + struct.pack("<I", 0)


def _parse_peer_init(payload):
    """Parse a PeerInit payload; returns (username, conn_type) or (None, None)."""
    username, offset = _unpack_string(payload, 0)
    if not username:
        return None, None
    conn_type, _ = _unpack_string(payload, offset)
    return username, conn_type


class PeerServer:
    """Listens for incoming peer ('P') and file ('F') connections.

    A 'P' connection delivers FileSearchResponse messages from peers that
    matched our search.  A 'F' connection is opened *by the uploader* and
    carries the raw file bytes for a transfer we requested.
    """

    def __init__(self, username, port=0):
        self.username = username
        self.port = port
        self._server = None
        self._stop = threading.Event()
        self.on_peer_search_response = None
        self.on_file_transfer = None
        self.on_indirect_request = None

    def start(self):
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind(('0.0.0.0', self.port))
            self._server.listen(16)
            self._server.settimeout(1)
            if self.port == 0:
                self.port = self._server.getsockname()[1]
            self._stop.clear()
            threading.Thread(target=self._accept_loop, daemon=True).start()
            logger.info("Peer server listening on port %d", self.port)
            return True
        except OSError as e:
            logger.warning("Peer server failed to start: %s", e)
            return False

    def stop(self):
        self._stop.set()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                sock, addr = self._server.accept()
                threading.Thread(target=self._handle_peer, args=(sock, addr), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_peer(self, sock, addr):
        """Perform the peer init handshake then dispatch by connection type.

        Returns without closing ``sock`` when ownership was handed off to a
        file-transfer thread, which is responsible for closing it.
        """
        try:
            sock.settimeout(TRANSFER_TIMEOUT)
            code, payload = _recv_init_message(sock)

            if code == CODE_INIT_PIERCE_FIREWALL:
                # An indirect connection request bounced back to us.  The
                # connection is already established, so nothing to do.
                token = struct.unpack("<I", payload[:4])[0] if len(payload) >= 4 else 0
                logger.debug("PierceFireWall from %s (token %d)", addr[0], token)
                if self.on_indirect_request:
                    self.on_indirect_request(token)
                return

            if code != CODE_INIT_PEER_INIT:
                logger.debug("Unknown init code %d from %s", code, addr[0])
                return

            peer_user, conn_type = _parse_peer_init(payload)
            if not peer_user:
                logger.debug("Malformed PeerInit from %s", addr[0])
                return

            logger.debug("Incoming %s connection from %s", conn_type, peer_user)
            _send_init_message(sock, CODE_INIT_PEER_INIT,
                               _pack_peer_init(self.username, conn_type))

            if conn_type == CONN_TYPE_FILE:
                if self._handle_file_connection(sock, peer_user):
                    return  # transfer thread owns the socket now
            else:
                self._handle_peer_connection(sock, peer_user)
        except (ConnectionError, OSError, struct.error, ValueError) as e:
            logger.debug("Peer %s disconnected: %s", addr[0], e)
        try:
            sock.close()
        except OSError:
            pass

    def _handle_peer_connection(self, sock, peer_user):
        while not self._stop.is_set():
            code, payload = _recv_message(sock)
            if code == CODE_P_SEARCH_RESPONSE:
                self._parse_search_results(payload, peer_user)

    def _handle_file_connection(self, sock, peer_user):
        """Receive a file the uploader opened to us.

        Wire format after the init handshake: FileTransferInit (uint32 token),
        then FileOffset (uint64) from us, then raw file bytes with no framing.

        Returns True when the socket was handed to a transfer thread, meaning
        the caller must not close it.
        """
        token_bytes = _read_exactly(sock, 4)
        token = struct.unpack("<I", token_bytes)[0]
        logger.info("Incoming file connection from %s for token %d", peer_user, token)
        if not self.on_file_transfer:
            return False
        self.on_file_transfer(token, peer_user, sock)
        return True

    def _parse_search_results(self, payload, peer_user):
        """Parse a FileSearchResponse (peer code 9) payload.

        The payload is zlib compressed and starts with the responding peer's
        username, then the search token and the result count.  Each result is
        preceded by a uint8 code byte.
        """
        try:
            raw = zlib.decompressobj().decompress(payload, MAX_DECOMPRESSED_SIZE)
        except zlib.error as e:
            logger.warning("Could not decompress search results from %s: %s", peer_user, e)
            return

        try:
            offset = 0
            _username, offset = _unpack_string(raw, offset)
            if offset + 8 > len(raw):
                return
            token = struct.unpack("<I", raw[offset:offset + 4])[0]
            offset += 4
            num_files = struct.unpack("<I", raw[offset:offset + 4])[0]
            offset += 4
            num_files = min(num_files, MAX_SEARCH_RESULTS)

            results = []
            for _ in range(num_files):
                if offset >= len(raw):
                    break
                offset += 1  # uint8 code, always 1
                filename, offset = _unpack_string(raw, offset)
                if offset + 8 > len(raw):
                    break
                size = struct.unpack("<Q", raw[offset:offset + 8])[0]
                offset += 8
                ext, offset = _unpack_string(raw, offset)
                if offset + 4 > len(raw):
                    break
                num_attrs = struct.unpack("<I", raw[offset:offset + 4])[0]
                offset += 4
                bitrate, duration = 0, 0
                for _ in range(min(num_attrs, 16)):
                    if offset + 8 > len(raw):
                        break
                    atype = struct.unpack("<I", raw[offset:offset + 4])[0]
                    aval = struct.unpack("<I", raw[offset + 4:offset + 8])[0]
                    offset += 8
                    if atype == 0:
                        bitrate = aval
                    elif atype == 1:
                        duration = aval
                if filename:
                    results.append({
                        "user": peer_user,
                        "filename": filename,
                        "size": size,
                        "extension": ext,
                        "bitrate": bitrate,
                        "length": duration,
                    })
            if results and self.on_peer_search_response:
                self.on_peer_search_response(results)
        except (struct.error, IndexError) as e:
            logger.error("Bad search results from %s: %s", peer_user, e)


class SoulseekProtocol:
    """Real Soulseek protocol handler using TCP sockets."""

    def __init__(self):
        self.server = DEFAULT_SERVER
        self.port = DEFAULT_PORT
        self.username = ""
        self.password = ""
        self.connected = False
        self._socket = None
        self._recv_thread = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._login_event = threading.Event()
        self._login_success = False
        self._ticket_counter = 0
        self._ticket_lock = threading.Lock()
        self._pending_downloads = {}

        self.peer_server = None
        self.peer_port = 0
        self.excluded_phrases = []

        self.on_message = None
        self.on_search_results = None
        self.on_connection = None
        self.on_error = None
        self.on_download_progress = None
        self.on_download_done = None

    def _next_ticket(self):
        """Monotonic token. Wall-clock seconds collide on rapid searches.

        Uses a dedicated lock because callers already hold ``self._lock``,
        which is not reentrant.
        """
        with self._ticket_lock:
            self._ticket_counter = (self._ticket_counter + 1) & 0xFFFFFFFF
            if self._ticket_counter == 0:
                self._ticket_counter = 1
            return self._ticket_counter

    def connect(self, username, password, server=None, port=None):
        self.username = username
        self.password = password
        if server:
            self.server = server
        if port:
            self.port = port
        self._stop_event.clear()
        thread = threading.Thread(target=self._connect_async, daemon=True)
        thread.start()
        return True

    def _connect_async(self):
        self._start_peer_server()
        attempts = 0
        while attempts < MAX_RECONNECT_ATTEMPTS and not self._stop_event.is_set():
            attempts += 1
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(SOCKET_TIMEOUT)
                sock.connect((self.server, self.port))
                sock.settimeout(None)
                _set_socket_keepalive(sock)
                with self._lock:
                    self._socket = sock
                self._login_event.clear()
                self._login_success = False
                self._send_login(sock)
                self._send_listen_port(sock, self.peer_port)
                self._start_receive(sock)
                if not self._login_event.wait(LOGIN_TIMEOUT):
                    raise ConnectionError("Login timed out")
                if not self._login_success:
                    raise ConnectionError("Login rejected by server")
                with self._lock:
                    self.connected = True
                logger.info("Connected to Soulseek at %s:%d as %s",
                            self.server, self.port, self.username)
                if self.on_connection:
                    self.on_connection(True)
                return
            except (socket.timeout, ConnectionRefusedError, OSError) as e:
                logger.warning("Connection attempt %d/%d failed: %s",
                               attempts, MAX_RECONNECT_ATTEMPTS, e)
                if self.on_error:
                    self.on_error(f"Connection failed (attempt {attempts}): {e}")
                self._cleanup("Retrying")
                if not self._stop_event.is_set():
                    backoff = RECONNECT_BACKOFF_BASE ** attempts
                    self._stop_event.wait(backoff)
        logger.error("All %d connection attempts failed", MAX_RECONNECT_ATTEMPTS)
        self._cleanup("All connection attempts failed")
        if self.on_error:
            self.on_error(f"Failed to connect after {MAX_RECONNECT_ATTEMPTS} attempts")

    def _start_peer_server(self):
        if self.peer_server:
            self.peer_server.stop()
        ps = PeerServer(self.username)
        if ps.start():
            ps.on_peer_search_response = self._on_peer_search_response
            ps.on_file_transfer = self._on_file_transfer
            self.peer_server = ps
            self.peer_port = ps.port
        else:
            self.peer_server = None
            self.peer_port = 0
            logger.warning("No peer server — search results and file data cannot be received")

    def _on_peer_search_response(self, results):
        if self.on_search_results:
            self.on_search_results(results)

    def _on_file_transfer(self, token, peer_user, sock):
        """Handle a uploader opening an 'F' connection for a queued download."""
        with self._lock:
            pending = self._pending_downloads.get(token)
        if not pending:
            logger.warning("File connection for unknown token %d — closing", token)
            try:
                sock.close()
            except OSError:
                pass
            return
        threading.Thread(
            target=self._receive_file_data,
            args=(pending, sock),
            daemon=True
        ).start()

    def _receive_file_data(self, pending, sock):
        """Stream an incoming 'F' connection to disk.

        We answer with FileOffset (uint64, always 0 — no resume) and then read
        raw bytes until the uploader stops or we hit the declared size.
        """
        ticket = pending["ticket"]
        output_path = pending["output_path"]
        expected = pending.get("size") or 0
        downloaded = 0
        try:
            sock.sendall(struct.pack("<Q", 0))  # FileOffset = 0
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'wb') as f:
                while True:
                    if expected and downloaded >= expected:
                        break
                    try:
                        chunk = sock.recv(CHUNK_SIZE)
                    except (socket.timeout, TimeoutError):
                        logger.warning("File transfer stalled for ticket %d", ticket)
                        break
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if self.on_download_progress:
                        self.on_download_progress(ticket, downloaded)

            if expected and downloaded < expected:
                logger.warning("Short transfer for ticket %d: %d/%d bytes",
                               ticket, downloaded, expected)
            if downloaded == 0:
                raise ConnectionError("No data received")

            logger.info("Downloaded %d bytes for ticket %d: %s",
                        downloaded, ticket, pending["filename"])
            self._finish_download(ticket, True)
        except (ConnectionError, OSError, ValueError) as e:
            logger.warning("File transfer failed for %s: %s", pending["filename"], e)
            self._finish_download(ticket, False, reason=str(e))
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _finish_download(self, ticket, success, reason=""):
        with self._lock:
            self._pending_downloads.pop(ticket, None)
        if not success:
            if self.on_error:
                self.on_error(f"Transfer failed: {reason}" if reason else "Transfer failed")
        if self.on_download_done:
            self.on_download_done(ticket, None, success)

    def _send_login(self, sock):
        md5_hash = hashlib.md5((self.username + self.password).encode()).hexdigest()
        payload = _pack_string(self.username) + _pack_string(self.password)
        payload += struct.pack("<I", CLIENT_MAJOR_VERSION)
        payload += _pack_string(md5_hash)
        payload += struct.pack("<I", CLIENT_MINOR_VERSION)
        _send_message(sock, CODE_LOGIN, payload)
        logger.debug("Login packet sent")

    def _send_post_login(self, sock):
        """Announce online status and shared-folder counts."""
        _send_message(sock, CODE_SET_STATUS, struct.pack("<i", 2))  # 2 = Online
        _send_message(sock, CODE_SHARED_FOLDERS, struct.pack("<II", 0, 0))
        _send_message(sock, CODE_HAVE_NO_PARENT, b"")
        logger.debug("Post-login handshake sent")

    def _send_listen_port(self, sock, port=0):
        """Tell the server which port we accept peer connections on."""
        _send_message(sock, CODE_SET_LISTEN_PORT, struct.pack("<I", port))
        logger.debug("Listen port sent: %d", port)

    def _start_receive(self, sock):
        self._recv_thread = threading.Thread(
            target=self._receive_loop, args=(sock,), daemon=True)
        self._recv_thread.start()

    def _receive_loop(self, sock):
        while not self._stop_event.is_set():
            try:
                code, payload = _recv_message(sock)
                self._handle_message(code, payload)
            except (ConnectionError, OSError, struct.error) as e:
                if not self._stop_event.is_set():
                    logger.warning("Receive error: %s", e)
                    if self.on_error:
                        self.on_error(f"Connection lost: {e}")
                break
        if not self._stop_event.is_set():
            self._cleanup("Receive thread ended")

    def _handle_message(self, code, payload):
        if code == CODE_LOGIN_RESP:
            self._handle_login_resp(payload)
        elif code == CODE_GET_PEER_ADDRESS_RESP:
            self._handle_peer_address_resp(payload)
        elif code == CODE_CONNECT_TO_PEER_RESP:
            self._handle_connect_to_peer(payload)
        elif code == CODE_PLACE_IN_QUEUE_RESP:
            self._handle_place_in_queue(payload)
        elif code == CODE_RELOGGED:
            logger.warning("Logged in elsewhere — disconnecting")
            if self.on_error:
                self.on_error("Logged in from another client")
            self.disconnect()
        elif code == CODE_SERVER_MESSAGE:
            msg, _ = _unpack_string(payload, 0)
            logger.info("Server message: %s", msg)
        elif code == CODE_EXCLUDED_SEARCH_PHRASES:
            self._handle_excluded_phrases(payload)
        elif code == CODE_WISHLIST_INTERVAL:
            if len(payload) >= 4:
                logger.debug("Wishlist interval: %d min",
                             struct.unpack("<I", payload[:4])[0])
        elif code == 32:  # ServerPing — the server no longer replies to these
            pass
        elif self.on_message:
            self.on_message(code, payload)

    def _handle_excluded_phrases(self, payload):
        """Store the server's list of phrases that must not be shared."""
        try:
            if len(payload) < 4:
                return
            count = struct.unpack("<I", payload[:4])[0]
            offset = 4
            phrases = []
            for _ in range(min(count, 10000)):
                phrase, offset = _unpack_string(payload, offset)
                if not phrase:
                    break
                phrases.append(phrase)
            self.excluded_phrases = phrases
            logger.info("Server supplied %d excluded search phrase(s)", len(phrases))
        except (struct.error, IndexError) as e:
            logger.error("Failed to parse excluded search phrases: %s", e)

    def _handle_login_resp(self, payload):
        if not payload:
            logger.error("Login response empty")
            self._login_success = False
            self._login_event.set()
            return
        success, offset = _unpack_bool(payload, 0)
        if success:
            greet, _ = _unpack_string(payload, offset)
            logger.info("Login successful: %s", greet[:80] if greet else "")
            with self._lock:
                sock = self._socket
            if sock:
                self._send_post_login(sock)
            self._login_success = True
        else:
            reason, _ = _unpack_string(payload, offset)
            logger.error("Login rejected: %s", reason)
            if self.on_error:
                self.on_error(f"Login rejected: {reason}")
            self._login_success = False
        self._login_event.set()

    def _cleanup(self, reason=""):
        if reason:
            logger.info("Cleanup: %s", reason)
        if self.peer_server:
            self.peer_server.stop()
            self.peer_server = None
            self.peer_port = 0
        with self._lock:
            was_connected = self.connected
            self.connected = False
            # Every pending transfer is dead once the server connection goes
            # away; keeping them leaks entries across reconnects.
            self._pending_downloads.clear()
            if self._socket:
                try:
                    self._socket.close()
                except OSError:
                    pass
                self._socket = None
        if was_connected and self.on_connection:
            self.on_connection(False)

    def disconnect(self):
        self._stop_event.set()
        self._cleanup("User requested disconnect")

    def search(self, query):
        with self._lock:
            sock = self._socket
            if not sock or not self.connected:
                logger.warning("Cannot search: not connected")
                return []
        ticket = self._next_ticket()
        payload = struct.pack("<I", ticket) + _pack_string(query)
        try:
            _send_message(sock, CODE_FILE_SEARCH, payload)
            logger.info("Search sent: ticket=%d query='%s'", ticket, query)
        except OSError as e:
            logger.error("Search send failed: %s", e)
            if self.on_error:
                self.on_error(f"Search failed: {e}")
            return []
        return ticket

    def _ip_to_str(self, ip_bytes):
        return socket.inet_ntoa(ip_bytes[::-1])

    def _handle_peer_address_resp(self, payload):
        """Server code 3: peer IP and port for a pending download."""
        try:
            username, offset = _unpack_string(payload, 0)
            if offset + 4 > len(payload):
                return
            ip = self._ip_to_str(payload[offset:offset + 4])
            offset += 4
            if offset + 4 <= len(payload):
                port = struct.unpack("<I", payload[offset:offset + 4])[0]
                offset += 4
            else:
                port = 0
            if not port or ip == "0.0.0.0":
                logger.warning("Peer %s is offline or has no listen port", username)
                if self.on_error:
                    self.on_error(f"User {username} is offline")
                return
            with self._lock:
                pending = [dl for dl in self._pending_downloads.values()
                           if dl.get("peer_user") == username]
            for dl in pending:
                threading.Thread(
                    target=self._request_peer_file,
                    args=(ip, port, dl["peer_user"], dl["filename"], dl["ticket"]),
                    daemon=True
                ).start()
        except (struct.error, IndexError) as e:
            logger.error("Failed to parse peer address response: %s", e)

    def _handle_connect_to_peer(self, payload):
        """Server code 18: another user wants to reach us indirectly.

        We respond by connecting to them and sending PierceFireWall with the
        token, so they can establish the 'P' connection from their side.
        """
        try:
            username, offset = _unpack_string(payload, 0)
            conn_type, offset = _unpack_string(payload, offset)
            if offset + 4 > len(payload):
                return
            ip = self._ip_to_str(payload[offset:offset + 4])
            offset += 4
            if offset + 4 > len(payload):
                return
            port = struct.unpack("<I", payload[offset:offset + 4])[0]
            offset += 4
            token = struct.unpack("<I", payload[offset:offset + 4])[0] if offset + 4 <= len(payload) else 0
            if not port or ip == "0.0.0.0":
                return
            threading.Thread(
                target=self._pierce_firewall,
                args=(ip, port, username, conn_type, token),
                daemon=True
            ).start()
        except (struct.error, IndexError) as e:
            logger.error("Failed to parse ConnectToPeer: %s", e)

    def _pierce_firewall(self, ip, port, username, conn_type, token):
        """Connect back to a peer and send PierceFireWall (init code 0)."""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(SOCKET_TIMEOUT)
            sock.connect((ip, port))
            _send_init_message(sock, CODE_INIT_PIERCE_FIREWALL, struct.pack("<I", token))
            logger.info("PierceFireWall sent to %s (token %d)", username, token)
        except OSError as e:
            logger.warning("PierceFireWall to %s failed: %s", username, e)
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

    def _handle_place_in_queue(self, payload):
        try:
            username, _ = _unpack_string(payload, 0)
            logger.info("Place in queue response from %s", username)
        except (struct.error, IndexError) as e:
            logger.error("Failed to parse place-in-queue: %s", e)

    def _request_peer_file(self, peer_ip, peer_port, peer_user, filename, ticket):
        """Open a 'P' connection to the peer and request the file.

        The exchange is: PeerInit handshake, then QueueUpload (peer code 43).
        The peer replies with TransferRequest (code 40) and we accept with
        TransferResponse (code 41); the bytes then arrive on a separate 'F'
        connection that the uploader opens to us.
        """
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(TRANSFER_TIMEOUT)
            sock.connect((peer_ip, peer_port))

            # Send our init before reading theirs: peers exchange PeerInit
            # simultaneously, so reading first deadlocks against any client
            # that writes first.
            _send_init_message(sock, CODE_INIT_PEER_INIT,
                               _pack_peer_init(self.username, CONN_TYPE_PEER))

            code, payload = _recv_init_message(sock)
            if code != CODE_INIT_PEER_INIT:
                raise ConnectionError("Unexpected init code %d from %s" % (code, peer_user))
            remote, _ = _parse_peer_init(payload)

            if remote and remote != peer_user:
                logger.warning("Expected %s but peer identified as %s", peer_user, remote)

            _send_message(sock, CODE_P_QUEUE_UPLOAD, _pack_string(filename))
            logger.info("Requested '%s' from %s (ticket %d)", filename, peer_user, ticket)

            # Stay on the connection to accept the TransferRequest.
            while not self._stop_event.is_set():
                pcode, ppayload = _recv_message(sock)
                if pcode == CODE_P_TRANSFER_REQUEST:
                    self._handle_peer_transfer_request(ticket, ppayload)
                    return
                if pcode in (CODE_P_UPLOAD_FAILED, CODE_P_UPLOAD_DENIED):
                    reason, _ = _unpack_string(ppayload, 0)
                    raise ConnectionError(reason or "Peer refused the transfer")
                if pcode == CODE_P_PLACE_IN_QUEUE_RESPONSE:
                    place = (struct.unpack("<I", ppayload[-4:])[0]
                             if len(ppayload) >= 4 else 0)
                    logger.info("Queued at position %d for ticket %d", place, ticket)
        except (ConnectionError, OSError, struct.error, ValueError) as e:
            logger.warning("Could not request file from %s: %s", peer_user, e)
            self._finish_download(ticket, False, reason=str(e))
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

    def _handle_peer_transfer_request(self, ticket, payload):
        """Accept a TransferRequest and record the expected file size."""
        try:
            if len(payload) < 12:
                raise ValueError("Truncated TransferRequest")
            direction = struct.unpack("<I", payload[:4])[0]
            transfer_token = struct.unpack("<I", payload[4:8])[0]
            filename, offset = _unpack_string(payload, 8)
            size = 0
            if direction == TRANSFER_DIRECTION_UPLOAD:
                if offset + 8 > len(payload):
                    raise ValueError("Truncated TransferRequest size")
                size = struct.unpack("<Q", payload[offset:offset + 8])[0]
            with self._lock:
                pending = self._pending_downloads.get(ticket)
                if pending is not None:
                    pending["size"] = size
            logger.info("Peer offers %s (%d bytes) token %d", filename, size, transfer_token)
        except (struct.error, ValueError) as e:
            logger.error("Failed to parse TransferRequest: %s", e)

    def download(self, username, filename, output_path=None, size=0):
        """Request a file from ``username``.

        Sends ConnectToPeer (so the peer can reach us if it is behind NAT) and
        GetPeerAddress; the transfer itself completes on a separate 'F'
        connection that the uploader opens.
        """
        with self._lock:
            sock = self._socket
            if not sock or not self.connected:
                logger.warning("Cannot download: not connected")
                return None
            ticket = self._next_ticket()
            out = output_path or os.path.join(
                os.getcwd(), os.path.basename(filename.replace('\\', '/')))
            self._pending_downloads[ticket] = {
                "peer_user": username,
                "filename": filename,
                "output_path": out,
                "ticket": ticket,
                "size": size or 0,
                "queued": False,
            }

        try:
            _send_message(sock, CODE_CONNECT_TO_PEER,
                          struct.pack("<I", ticket) + _pack_string(username)
                          + _pack_string(CONN_TYPE_PEER))
            _send_message(sock, CODE_GET_PEER_ADDRESS, _pack_string(username))
            logger.info("Download queued: ticket=%d file='%s' from %s",
                        ticket, filename, username)
            return ticket
        except OSError as e:
            logger.error("Download request failed: %s", e)
            with self._lock:
                self._pending_downloads.pop(ticket, None)
            if self.on_error:
                self.on_error(f"Download request failed: {e}")
            return None


class P2PManager:
    """Integrated P2P manager using Soulseek protocol."""

    def __init__(self, status_callback=None, search_callback=None,
                 download_callback=None, error_callback=None,
                 connection_callback=None, transfer_callback=None):
        self.protocol = SoulseekProtocol()
        self.status_callback = status_callback
        self.search_callback = search_callback
        self.download_callback = download_callback
        self.error_callback = error_callback
        self.connection_callback = connection_callback
        self.transfer_callback = transfer_callback
        self.search_results = []
        self.downloads = []
        self._lock = threading.Lock()
        self._setup_callbacks()

    def _setup_callbacks(self):
        def on_connection(status):
            self.log("Connected to Soulseek" if status else "Disconnected from Soulseek")
            if self.connection_callback:
                self.connection_callback(status)

        def on_search_results(results):
            with self._lock:
                self.search_results.extend(results)
            self.log(f"Received {len(results)} search result(s)")
            if self.search_callback:
                for r in results:
                    self.search_callback(r)

        def on_error(msg):
            logger.error("P2P error: %s", msg)
            if self.error_callback:
                self.error_callback(msg)

        def on_download_progress(ticket, downloaded):
            if self.transfer_callback:
                self.transfer_callback(ticket, downloaded, None, "downloading")

        def on_download_done(ticket, path, success):
            status = "complete" if success else "failed"
            with self._lock:
                for entry in self.downloads:
                    if entry.get("ticket") == ticket:
                        entry["status"] = status
                        if success and path:
                            entry["path"] = path
            if self.transfer_callback:
                self.transfer_callback(ticket, 0, path, status)
            name = os.path.basename(path) if path else f"ticket #{ticket}"
            self.log(f"Download {status}: {name}")

        self.protocol.on_connection = on_connection
        self.protocol.on_search_results = on_search_results
        self.protocol.on_error = on_error
        self.protocol.on_download_progress = on_download_progress
        self.protocol.on_download_done = on_download_done

    def log(self, message):
        if self.status_callback:
            self.status_callback(message)

    def connect(self, username, password, server=DEFAULT_SERVER, port=DEFAULT_PORT):
        self.protocol.connect(username, password, server, port)
        return True

    def disconnect(self):
        self.protocol.disconnect()

    def search_files(self, query):
        if not self.is_connected:
            self.log("Not connected to Soulseek")
            return None
        # Results accumulate per search; reset so a long session cannot grow
        # this list without bound.
        with self._lock:
            self.search_results = []
        ticket = self.protocol.search(query)
        if ticket:
            self.log(f"Searching for '{query}' (ticket #{ticket})")
        else:
            self.log("Search failed to send")
        return ticket

    #: Cap on retained transfer records; terminal ones are dropped oldest-first
    #: so a long session cannot grow `downloads` without bound.
    _MAX_DOWNLOAD_RECORDS = 200
    _TERMINAL_TRANSFER = ("complete", "failed")

    def _prune_downloads(self):
        """Trim finished transfer records. Caller must hold `_lock`.

        In-flight entries are never dropped, because the GUI looks up a
        transfer's expected size from this list while it is still running.
        """
        if len(self.downloads) <= self._MAX_DOWNLOAD_RECORDS:
            return
        finished = [
            i for i, e in enumerate(self.downloads)
            if e.get("status") in self._TERMINAL_TRANSFER
        ]
        excess = len(self.downloads) - self._MAX_DOWNLOAD_RECORDS
        for i in finished[:excess]:
            self.downloads.pop(i)

    def download_file(self, username, filename, output_dir=None, size=0):
        if not self.is_connected:
            self.log("Not connected to Soulseek")
            return None
        out = output_dir or os.getcwd()
        out_path = os.path.join(out, os.path.basename(filename.replace('\\', '/')))
        ticket = self.protocol.download(username, filename, out_path, size=size)
        if ticket is not None:
            with self._lock:
                self.downloads.append({
                    "user": username,
                    "filename": filename,
                    "status": "queued",
                    "ticket": ticket,
                    "path": out_path,
                    "downloaded": 0,
                    "size": size or 0,
                })
                self._prune_downloads()
            self.log(f"Download queued: {filename} from {username} (ticket #{ticket})")
            if self.download_callback:
                self.download_callback(username, filename, ticket)
        else:
            self.log("Download failed to send")
        return ticket

    @property
    def is_connected(self):
        with self._lock:
            return self.protocol.connected
