"""
Relay file transfer — send/receive files through hub relay sessions.

Uses NMDCpb relay protocol (PbRelayRequest/Ack/Data/Closed) to transfer
files between two users via the hub, with optional E2E encryption.

The hub acts as a transparent forwarder. This is designed for passive users
(both behind NAT) who cannot establish direct connections.

Usage:
    # Sender
    transfer = RelayFileTransfer(client)
    await transfer.send_file("target_nick", "/path/to/file.txt")

    # Receiver (auto-accept mode)
    transfer = RelayFileTransfer(client, auto_accept=True, download_dir="/tmp")
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from verlihub.client.nmdcpb.client import NMDCpbClient

log = logging.getLogger(__name__)

# Maximum chunk size for relay data (must fit within hub's relay_max_payload)
DEFAULT_CHUNK_SIZE = 32768  # 32 KB

# Transfer metadata header: magic(4) + name_len(2) + size(8) + sha256(32)
HEADER_MAGIC = b"RFXR"
HEADER_FMT = "!4sH Q 32s"  # magic, name_len, file_size, sha256_hash
HEADER_SIZE = struct.calcsize(HEADER_FMT)


class TransferState(Enum):
    """State of a relay file transfer."""
    PENDING = "pending"          # Request sent, waiting for ack
    NEGOTIATING = "negotiating"  # Ack received, sending header
    TRANSFERRING = "transferring"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TransferInfo:
    """Metadata about a file transfer."""
    relay_id: int = 0
    token: str = ""
    peer_nick: str = ""
    filename: str = ""
    file_size: int = 0
    sha256: bytes = b""
    bytes_transferred: int = 0
    state: TransferState = TransferState.PENDING
    started_at: float = 0.0
    error: str = ""

    @property
    def progress(self) -> float:
        if self.file_size == 0:
            return 0.0
        return min(1.0, self.bytes_transferred / self.file_size)

    @property
    def speed_bps(self) -> float:
        elapsed = time.time() - self.started_at if self.started_at else 0
        return self.bytes_transferred / elapsed if elapsed > 0 else 0.0


class RelayFileTransfer:
    """High-level relay file transfer manager.

    Handles chunked file transfer over NMDCpb relay sessions:
    - Sender: reads file, sends header + chunks via relay_data
    - Receiver: reassembles chunks, verifies SHA-256

    Thread safety: all methods are async and should be called from
    the same event loop as the NMDCpbClient.
    """

    def __init__(
        self,
        client: "NMDCpbClient",
        auto_accept: bool = False,
        download_dir: str = "/tmp",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ):
        self.client = client
        self.auto_accept = auto_accept
        self.download_dir = download_dir
        self.chunk_size = chunk_size

        # Active transfers
        self._sending: dict[int, _SendContext] = {}      # relay_id → context
        self._receiving: dict[int, _RecvContext] = {}     # relay_id → context
        self._transfers: dict[int, TransferInfo] = {}     # relay_id → info

        # Callbacks
        self.on_transfer_request: Optional[Callable[[TransferInfo], None]] = None
        self.on_transfer_progress: Optional[Callable[[TransferInfo], None]] = None
        self.on_transfer_complete: Optional[Callable[[TransferInfo], None]] = None
        self.on_transfer_failed: Optional[Callable[[TransferInfo], None]] = None

        # Hook into client callbacks
        self._orig_on_relay_request = client.on_relay_request
        self._orig_on_relay_established = client.on_relay_established
        self._orig_on_relay_data = client.on_relay_data
        self._orig_on_relay_closed = client.on_relay_closed
        self._orig_on_relay_resume = client.on_relay_resume
        client.on_relay_request = self._on_relay_request
        client.on_relay_established = self._on_relay_established
        client.on_relay_data = self._on_relay_data
        client.on_relay_closed = self._on_relay_closed
        client.on_relay_resume = self._on_relay_resume

    def detach(self) -> None:
        """Unhook from client callbacks."""
        self.client.on_relay_request = self._orig_on_relay_request
        self.client.on_relay_established = self._orig_on_relay_established
        self.client.on_relay_data = self._orig_on_relay_data
        self.client.on_relay_closed = self._orig_on_relay_closed
        self.client.on_relay_resume = self._orig_on_relay_resume

    # --- Public API ---

    async def send_file(self, target_nick: str, filepath: str) -> TransferInfo:
        """Initiate a file transfer to target_nick.

        Returns TransferInfo that will be updated as the transfer progresses.
        """
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        file_size = os.path.getsize(filepath)
        filename = os.path.basename(filepath)

        # Compute SHA-256
        sha = hashlib.sha256()
        with open(filepath, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                sha.update(block)
        file_hash = sha.digest()

        info = TransferInfo(
            peer_nick=target_nick,
            filename=filename,
            file_size=file_size,
            sha256=file_hash,
            state=TransferState.PENDING,
            started_at=time.time(),
        )

        token = await self.client.request_relay(
            target_nick,
            purpose="FILE_TRANSFER",
            estimated_size=file_size,
        )
        info.token = token

        ctx = _SendContext(filepath=filepath, info=info)
        self._sending[token] = ctx  # keyed by token until we get relay_id

        log.info(f"File transfer initiated: {filename} ({file_size} bytes) → {target_nick}")
        return info

    def get_transfer(self, relay_id: int) -> Optional[TransferInfo]:
        """Get transfer info by relay_id."""
        return self._transfers.get(relay_id)

    def list_transfers(self) -> list[TransferInfo]:
        """List all active/completed transfers."""
        return list(self._transfers.values())

    async def request_resume(self, relay_id: int) -> bool:
        """Request the sender to resume transfer from current offset.

        Used when a transfer is interrupted but the relay session is still alive.
        Sends a PbRelayResume with the receiver's current offset and partial SHA-256.

        Returns True if resume request was sent.
        """
        ctx = self._receiving.get(relay_id)
        if not ctx or not ctx.header_received:
            log.warning(f"Cannot resume: no active receive context for relay {relay_id}")
            return False

        info = ctx.info
        resume_offset = info.bytes_transferred
        partial_sha = ctx.sha.digest() if hasattr(ctx.sha, 'digest') else b""

        ok = await self.client.send_relay_resume(
            relay_id, resume_offset, partial_sha,
        )
        if ok:
            info.state = TransferState.TRANSFERRING
            info.error = ""
            log.info(f"Resume requested for relay {relay_id} at offset {resume_offset}")
        return ok

    # --- Callback Handlers ---

    def _on_relay_request(self, from_nick: str, token: str, purpose: str, est_size: int) -> None:
        """Handle incoming relay/file-transfer request."""
        if purpose != "FILE_TRANSFER":
            # Not a file transfer — pass through
            if self._orig_on_relay_request:
                self._orig_on_relay_request(from_nick, token, purpose, est_size)
            return

        info = TransferInfo(
            token=token,
            peer_nick=from_nick,
            file_size=est_size,
            state=TransferState.PENDING,
            started_at=time.time(),
        )

        if self.on_transfer_request:
            self.on_transfer_request(info)

        if self.auto_accept:
            asyncio.ensure_future(self.client.accept_relay(token))
            log.info(f"Auto-accepted file transfer from {from_nick}")
        else:
            log.info(f"File transfer request from {from_nick}: {est_size} bytes (token={token})")

        # Store for when ack comes back with relay_id
        ctx = _RecvContext(info=info, download_dir=self.download_dir)
        self._receiving[token] = ctx  # keyed by token temporarily

    def _on_relay_established(self, relay_id: int, peer_nick: str) -> None:
        """Handle relay session established — start sending."""
        # Check if this is a send context (match by token via client's session)
        client_sess = self.client._relay_sessions.get(relay_id, {})
        token = client_sess.get("token", "")

        # Check send contexts
        if token in self._sending:
            ctx = self._sending.pop(token)
            ctx.info.relay_id = relay_id
            ctx.info.state = TransferState.NEGOTIATING
            self._transfers[relay_id] = ctx.info
            self._sending[relay_id] = ctx
            asyncio.ensure_future(self._do_send(relay_id, ctx))
            return

        # Check receive contexts
        if token in self._receiving:
            ctx = self._receiving.pop(token)
            ctx.info.relay_id = relay_id
            ctx.info.state = TransferState.NEGOTIATING
            self._transfers[relay_id] = ctx.info
            self._receiving[relay_id] = ctx
            return

    def _on_relay_data(self, relay_id: int, data: bytes, offset: int) -> None:
        """Handle incoming relay data — accumulate for file receive."""
        ctx = self._receiving.get(relay_id)
        if not ctx:
            log.debug(f"Relay data for non-receive session {relay_id}")
            return

        asyncio.ensure_future(self._do_receive_chunk(relay_id, ctx, data))

    def _on_relay_resume(self, relay_id: int, resume_offset: int,
                          partial_sha256: bytes) -> None:
        """Handle resume request from receiver — restart sending from offset."""
        ctx = self._sending.get(relay_id)
        if not ctx:
            log.warning(f"Relay resume for non-send session {relay_id}")
            if self._orig_on_relay_resume:
                self._orig_on_relay_resume(relay_id, resume_offset, partial_sha256)
            return

        info = ctx.info
        if info.state not in (TransferState.TRANSFERRING, TransferState.FAILED):
            log.warning(f"Relay resume for session {relay_id} in state {info.state}")
            return

        log.info(f"Resuming send for relay {relay_id} from offset {resume_offset}")
        info.state = TransferState.TRANSFERRING
        info.bytes_transferred = resume_offset
        info.error = ""
        asyncio.ensure_future(self._do_send(relay_id, ctx, resume_offset=resume_offset))

    def _on_relay_closed(self, relay_id: int, reason: str) -> None:
        """Handle relay session closed."""
        info = self._transfers.get(relay_id)
        if info and info.state == TransferState.TRANSFERRING:
            if reason == "NORMAL":
                info.state = TransferState.COMPLETED
            else:
                info.state = TransferState.FAILED
                info.error = f"Relay closed: {reason}"
                if self.on_transfer_failed:
                    self.on_transfer_failed(info)

        self._sending.pop(relay_id, None)
        self._receiving.pop(relay_id, None)

    # --- Send Logic ---

    async def _do_send(self, relay_id: int, ctx: "_SendContext",
                       resume_offset: int = 0) -> None:
        """Send file header + data chunks through relay.

        Args:
            relay_id: The relay session ID.
            ctx: Send context with filepath and transfer info.
            resume_offset: If >0, skip the header and seek to this byte offset
                          in the file before sending data chunks (for resume).
        """
        try:
            info = ctx.info
            filename_bytes = info.filename.encode("utf-8")

            if resume_offset == 0:
                # Build and send header (only for fresh transfers)
                header = struct.pack(
                    HEADER_FMT,
                    HEADER_MAGIC,
                    len(filename_bytes),
                    info.file_size,
                    info.sha256,
                )
                header += filename_bytes

                info.state = TransferState.TRANSFERRING
                await self.client.send_relay_data(relay_id, header, 0)

            # Send file data in chunks
            offset = resume_offset
            with open(ctx.filepath, "rb") as f:
                if resume_offset > 0:
                    f.seek(resume_offset)
                while True:
                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break

                    sent = await self.client.send_relay_data(relay_id, chunk, offset)
                    if not sent:
                        info.state = TransferState.FAILED
                        info.error = "Send failed"
                        if self.on_transfer_failed:
                            self.on_transfer_failed(info)
                        return

                    offset += len(chunk)
                    info.bytes_transferred = offset

                    if self.on_transfer_progress:
                        self.on_transfer_progress(info)

                    # Small yield to avoid blocking event loop
                    await asyncio.sleep(0)

            info.state = TransferState.COMPLETED
            log.info(f"File sent: {info.filename} ({offset} bytes) to {info.peer_nick}")

            if self.on_transfer_complete:
                self.on_transfer_complete(info)

            # Close relay session after transfer
            await self.client.close_relay(relay_id, "NORMAL")

        except Exception as e:
            log.error(f"Send error: {e}")
            info = ctx.info
            info.state = TransferState.FAILED
            info.error = str(e)
            if self.on_transfer_failed:
                self.on_transfer_failed(info)

    # --- Receive Logic ---

    async def _do_receive_chunk(self, relay_id: int, ctx: "_RecvContext", data: bytes) -> None:
        """Process an incoming relay data chunk."""
        try:
            info = ctx.info

            if not ctx.header_received:
                # Accumulate until we have the full header
                ctx.buffer += data

                if len(ctx.buffer) >= HEADER_SIZE:
                    magic, name_len, file_size, sha256 = struct.unpack(
                        HEADER_FMT, ctx.buffer[:HEADER_SIZE]
                    )
                    if magic != HEADER_MAGIC:
                        info.state = TransferState.FAILED
                        info.error = "Invalid header magic"
                        if self.on_transfer_failed:
                            self.on_transfer_failed(info)
                        return

                    full_header_size = HEADER_SIZE + name_len
                    if len(ctx.buffer) < full_header_size:
                        return  # Need more data for filename

                    filename = ctx.buffer[HEADER_SIZE:full_header_size].decode("utf-8")
                    remaining = ctx.buffer[full_header_size:]

                    info.filename = filename
                    info.file_size = file_size
                    info.sha256 = sha256
                    info.state = TransferState.TRANSFERRING

                    ctx.header_received = True
                    ctx.buffer = b""
                    ctx.sha = hashlib.sha256()

                    # Sanitize filename and open output file
                    safe_name = os.path.basename(filename)
                    if not safe_name:
                        safe_name = f"relay_{relay_id}"
                    ctx.output_path = os.path.join(ctx.download_dir, safe_name)
                    ctx.file = open(ctx.output_path, "wb")

                    log.info(f"Receiving file: {filename} ({file_size} bytes) from {info.peer_nick}")

                    if self.on_transfer_request:
                        self.on_transfer_request(info)

                    # Process any data beyond the header
                    if remaining:
                        await self._write_chunk(relay_id, ctx, remaining)
            else:
                await self._write_chunk(relay_id, ctx, data)

        except Exception as e:
            log.error(f"Receive error: {e}")
            info = ctx.info
            info.state = TransferState.FAILED
            info.error = str(e)
            if self.on_transfer_failed:
                self.on_transfer_failed(info)

    async def _write_chunk(self, relay_id: int, ctx: "_RecvContext", data: bytes) -> None:
        """Write a data chunk to the output file."""
        info = ctx.info

        if ctx.file:
            ctx.file.write(data)
            ctx.sha.update(data)
            info.bytes_transferred += len(data)

            if self.on_transfer_progress:
                self.on_transfer_progress(info)

            # Check if transfer is complete
            if info.bytes_transferred >= info.file_size:
                ctx.file.close()
                ctx.file = None

                # Verify SHA-256
                if ctx.sha.digest() == info.sha256:
                    info.state = TransferState.COMPLETED
                    log.info(f"File received: {info.filename} — SHA-256 verified ✓")
                    if self.on_transfer_complete:
                        self.on_transfer_complete(info)
                else:
                    info.state = TransferState.FAILED
                    info.error = "SHA-256 mismatch"
                    log.error(f"File received but SHA-256 mismatch: {info.filename}")
                    if self.on_transfer_failed:
                        self.on_transfer_failed(info)

                await self.client.close_relay(relay_id, "NORMAL")


@dataclass
class _SendContext:
    """Internal state for an outgoing file transfer."""
    filepath: str = ""
    info: TransferInfo = field(default_factory=TransferInfo)


@dataclass
class _RecvContext:
    """Internal state for an incoming file transfer."""
    download_dir: str = "/tmp"
    info: TransferInfo = field(default_factory=TransferInfo)
    header_received: bool = False
    buffer: bytes = b""
    sha: object = field(default_factory=lambda: hashlib.sha256())
    output_path: str = ""
    file: object = None


# ============================================================================
# Segmented Multi-Source Download Coordinator
# ============================================================================

# Minimum segment size (256KB) to avoid overhead from too-small segments
MIN_SEGMENT_SIZE = 262144


class SegmentState(Enum):
    """State of a download segment."""
    PENDING = "pending"
    ASSIGNED = "assigned"
    TRANSFERRING = "transferring"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Segment:
    """A single segment of a multi-source download."""
    index: int
    offset: int
    length: int
    state: SegmentState = SegmentState.PENDING
    peer_nick: str = ""
    relay_id: int = 0
    bytes_received: int = 0
    retries: int = 0


@dataclass
class SegmentedDownloadInfo:
    """Metadata for a segmented multi-source download."""
    file_tth: str = ""
    file_size: int = 0
    segment_count: int = 0
    download_dir: str = "/tmp"
    segments: list = field(default_factory=list)
    started_at: float = 0.0
    completed_at: float = 0.0
    error: str = ""

    @property
    def bytes_received(self) -> int:
        return sum(s.bytes_received for s in self.segments)

    @property
    def progress(self) -> float:
        if self.file_size == 0:
            return 0.0
        return min(1.0, self.bytes_received / self.file_size)

    @property
    def completed_segments(self) -> int:
        return sum(1 for s in self.segments
                   if s.state == SegmentState.COMPLETED)

    @property
    def is_complete(self) -> bool:
        return all(s.state == SegmentState.COMPLETED for s in self.segments)

    @property
    def active_peers(self) -> list[str]:
        return list({s.peer_nick for s in self.segments
                     if s.state in (SegmentState.ASSIGNED,
                                    SegmentState.TRANSFERRING) and s.peer_nick})


class SegmentCoordinator:
    """Coordinates multi-source segmented downloads.

    Splits a file into segments and assigns them to different peers.
    Each peer serves its segment through a separate relay session.

    Usage:
        coord = SegmentCoordinator(file_tth="ABC123", file_size=10_000_000,
                                   peers=["Alice", "Bob", "Carol"])
        coord.plan_segments()

        # For each segment, request a relay session with the assigned peer
        for seg in coord.pending_segments():
            await client.request_relay(seg.peer_nick, ...)

        # As relay data arrives, feed it to the coordinator
        coord.on_segment_data(segment_index, data, offset)

        # Check completion
        if coord.info.is_complete:
            ...
    """

    MAX_RETRIES = 3

    def __init__(
        self,
        file_tth: str,
        file_size: int,
        peers: list[str],
        segment_size: int = 0,
        download_dir: str = "/tmp",
    ):
        self.file_tth = file_tth
        self.file_size = file_size
        self.peers = list(peers)
        self.download_dir = download_dir

        # Auto-compute segment size based on peers and file size
        if segment_size > 0:
            self._segment_size = max(segment_size, MIN_SEGMENT_SIZE)
        else:
            # Default: split across peers, with minimum segment size
            ideal = file_size // max(len(peers), 1)
            self._segment_size = max(ideal, MIN_SEGMENT_SIZE)

        self.info = SegmentedDownloadInfo(
            file_tth=file_tth,
            file_size=file_size,
            download_dir=download_dir,
            started_at=time.time(),
        )
        self._segments: list[Segment] = []

        # Callbacks
        self.on_segment_complete: Optional[Callable[[Segment], None]] = None
        self.on_segment_failed: Optional[Callable[[Segment], None]] = None
        self.on_download_complete: Optional[Callable[[SegmentedDownloadInfo], None]] = None

    def plan_segments(self) -> list[Segment]:
        """Split the file into segments and assign to peers round-robin.

        Returns the list of planned segments.
        """
        self._segments = []
        offset = 0
        index = 0

        while offset < self.file_size:
            length = min(self._segment_size, self.file_size - offset)
            peer = self.peers[index % len(self.peers)] if self.peers else ""
            seg = Segment(
                index=index,
                offset=offset,
                length=length,
                peer_nick=peer,
                state=SegmentState.PENDING,
            )
            self._segments.append(seg)
            offset += length
            index += 1

        self.info.segments = self._segments
        self.info.segment_count = len(self._segments)
        log.info(f"Planned {len(self._segments)} segments for {self.file_tth} "
                 f"({self.file_size} bytes, {len(self.peers)} peers)")
        return list(self._segments)

    def pending_segments(self) -> list[Segment]:
        """Return segments that need to be started."""
        return [s for s in self._segments if s.state == SegmentState.PENDING]

    def assign_segment(self, index: int, peer_nick: str, relay_id: int) -> None:
        """Mark a segment as assigned to a peer with an established relay."""
        seg = self._segments[index]
        seg.state = SegmentState.ASSIGNED
        seg.peer_nick = peer_nick
        seg.relay_id = relay_id

    def start_segment(self, index: int) -> None:
        """Mark segment as actively transferring."""
        self._segments[index].state = SegmentState.TRANSFERRING

    def on_segment_data(self, index: int, data: bytes) -> None:
        """Process incoming data for a segment."""
        seg = self._segments[index]
        if seg.state not in (SegmentState.ASSIGNED, SegmentState.TRANSFERRING):
            return
        seg.state = SegmentState.TRANSFERRING
        seg.bytes_received += len(data)

        if seg.bytes_received >= seg.length:
            seg.state = SegmentState.COMPLETED
            if self.on_segment_complete:
                self.on_segment_complete(seg)
            if self.info.is_complete:
                self.info.completed_at = time.time()
                if self.on_download_complete:
                    self.on_download_complete(self.info)

    def fail_segment(self, index: int, error: str = "") -> bool:
        """Mark a segment as failed. Returns True if it can be retried."""
        seg = self._segments[index]
        seg.retries += 1
        if seg.retries <= self.MAX_RETRIES:
            seg.state = SegmentState.PENDING
            seg.peer_nick = ""
            seg.relay_id = 0
            seg.bytes_received = 0
            return True
        seg.state = SegmentState.FAILED
        if self.on_segment_failed:
            self.on_segment_failed(seg)
        return False

    def reassign_peer(self, old_peer: str, new_peer: str) -> list[Segment]:
        """Reassign all segments from old_peer to new_peer (e.g., on disconnect).

        Returns list of reassigned segments.
        """
        reassigned = []
        for seg in self._segments:
            if seg.peer_nick == old_peer and seg.state in (
                SegmentState.PENDING, SegmentState.ASSIGNED
            ):
                seg.peer_nick = new_peer
                seg.state = SegmentState.PENDING
                seg.relay_id = 0
                reassigned.append(seg)
        return reassigned

    def get_segment_by_relay(self, relay_id: int) -> Optional[Segment]:
        """Find a segment by its relay_id."""
        for seg in self._segments:
            if seg.relay_id == relay_id:
                return seg
        return None
