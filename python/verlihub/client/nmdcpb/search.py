"""Stealth search — aggregate file search results from multiple NMDCpb peers.

Uses PbUserQuery to discover NMDCpb-capable peers, then sweeps a
PbPrivateSearch to each.  Results are collected, de-duplicated by TTH,
and ranked by slot availability.

Usage::

    search = StealthSearch(client, query="Ubuntu ISO", max_peers=20)
    results = await search.run(timeout=15.0)
    for r in results:
        print(f"{r.filename}  TTH:{r.tth}  from {r.peers}")
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .client import NMDCpbClient

log = logging.getLogger(__name__)


# =========================================================================
# Result data
# =========================================================================

@dataclass
class SearchHit:
    """A single file matching a stealth search, possibly offered by multiple peers."""
    filename: str = ""
    path: str = ""               # full share path
    size: int = 0
    tth: str = ""                # base32 TTH root hash
    is_directory: bool = False
    peers: list[str] = field(default_factory=list)  # nicks offering this file
    best_free_slots: int = 0
    best_total_slots: int = 0

    @property
    def unique_key(self) -> str:
        """Key used for dedup: TTH + size (directories hit by path+size)."""
        if self.tth:
            return f"{self.tth}:{self.size}"
        return f"{self.path}/{self.filename}:{self.size}"


# =========================================================================
# StealthSearch
# =========================================================================

class StealthSearch:
    """Orchestrates a stealth multi-peer file search.

    Lifecycle::

        search = StealthSearch(client, query="ubuntu", max_peers=20)
        results = await search.run(timeout=15)

    The search is one-shot: call ``run()`` once, then inspect ``results``.
    """

    # Rate-limit: minimum seconds between consecutive stealth searches
    MIN_INTERVAL: float = 2.0
    _last_search_time: float = 0.0

    def __init__(
        self,
        client: "NMDCpbClient",
        query: str = "",
        tth: str = "",
        max_peers: int = 50,
        max_results_per_peer: int = 10,
        min_share_size: int = 0,
    ):
        if not query and not tth:
            raise ValueError("Either query or tth must be provided")

        self.client = client
        self.query = query
        self.tth = tth
        self.max_peers = max_peers
        self.max_results_per_peer = max_results_per_peer
        self.min_share_size = min_share_size

        self.query_id = str(uuid.uuid4())[:8]
        self.results: list[SearchHit] = []
        self._hits: dict[str, SearchHit] = {}          # unique_key → hit
        self._peers_responded: set[str] = set()
        self._peers_expected: int = 0
        self._done_event = asyncio.Event()
        self._started = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, timeout: float = 15.0) -> list[SearchHit]:
        """Execute the search and return de-duped, ranked results.

        Blocks until all peers respond or *timeout* seconds elapse.
        """
        if self._started:
            raise RuntimeError("StealthSearch is single-use; create a new instance")
        self._started = True

        # Rate limiting
        now = time.monotonic()
        elapsed = now - StealthSearch._last_search_time
        if elapsed < self.MIN_INTERVAL:
            await asyncio.sleep(self.MIN_INTERVAL - elapsed)
        StealthSearch._last_search_time = time.monotonic()

        # Install our callback
        orig_uqr = self.client.on_user_query_result
        orig_psr = self.client.on_private_search_result
        self.client.on_user_query_result = self._on_user_query_result
        self.client.on_private_search_result = self._on_private_search_result

        try:
            # Phase 1: discover NMDCpb-capable peers via user query + sweep
            await self.client.send_user_query(
                query_id=self.query_id,
                feature_filter="NMDCpb",
                min_share_size=self.min_share_size,
                max_results=self.max_peers,
                sweep=True,
                search_query=self.query,
                search_tth=self.tth,
            )
            log.info(f"Stealth search sent: id={self.query_id} q={self.query or self.tth}")

            # Phase 2: wait for results
            try:
                await asyncio.wait_for(self._done_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                log.debug(
                    f"Search {self.query_id} timed out: "
                    f"{len(self._peers_responded)}/{self._peers_expected} peers responded"
                )

        finally:
            # Restore original callbacks
            self.client.on_user_query_result = orig_uqr
            self.client.on_private_search_result = orig_psr

        # Rank: prefer files available from more peers, break tie by free slots
        self.results = sorted(
            self._hits.values(),
            key=lambda h: (-len(h.peers), -h.best_free_slots, h.filename),
        )
        log.info(
            f"Search {self.query_id} complete: "
            f"{len(self.results)} unique results from "
            f"{len(self._peers_responded)} peers"
        )
        return self.results

    # ------------------------------------------------------------------
    # Internal callbacks
    # ------------------------------------------------------------------

    def _on_user_query_result(self, result) -> None:
        """Handle PbUserQueryResult — tells us how many peers will respond."""
        if result.query_id != self.query_id:
            return

        if result.error:
            log.warning(f"User query error: {result.error}")
            self._done_event.set()
            return

        self._peers_expected = result.sweep_count
        log.debug(
            f"UserQueryResult: {result.total_matching} matching, "
            f"sweep sent to {result.sweep_count}"
        )

        if result.sweep_count == 0:
            self._done_event.set()

    def _on_private_search_result(self, from_nick: str, result) -> None:
        """Handle incoming PbPrivateSearchResult from a peer."""
        if result.search_id != self.query_id:
            return

        self._peers_responded.add(from_nick)

        for r in result.results:
            hit_key = SearchHit(
                filename=r.filename, path=r.path,
                size=r.size, tth=r.tth, is_directory=r.is_directory,
            ).unique_key

            if hit_key in self._hits:
                hit = self._hits[hit_key]
                if from_nick not in hit.peers:
                    hit.peers.append(from_nick)
                hit.best_free_slots = max(hit.best_free_slots, r.free_slots)
                hit.best_total_slots = max(hit.best_total_slots, r.total_slots)
            else:
                hit = SearchHit(
                    filename=r.filename,
                    path=r.path,
                    size=r.size,
                    tth=r.tth,
                    is_directory=r.is_directory,
                    peers=[from_nick],
                    best_free_slots=r.free_slots,
                    best_total_slots=r.total_slots,
                )
                self._hits[hit_key] = hit

        # Check if all expected peers have responded
        if self._peers_expected and len(self._peers_responded) >= self._peers_expected:
            self._done_event.set()
