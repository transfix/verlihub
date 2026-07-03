"""
Comprehensive tests for the hublist feature.

Covers:
1. HubListEntry model CRUD (create, read, update, delete)
2. Hublist server endpoints (GET /, GET /stats, POST /register, DELETE /{id})
3. Registration via form-encoded data (NMDC standard)
4. Registration via JSON body
5. Upsert behaviour (same address updates, different address creates)
6. Stale hub pruning
7. XML and JSON response formats
8. Registration client (unit tests with mocked HTTP)
9. Input validation (missing name, missing address)
10. HubListConfig dataclass and YAML round-trip
11. build_hub_info() helper
12. Dashboard config hublist fields
"""

import asyncio
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import httpx
from httpx import AsyncClient, Response as HttpxResponse, Request as HttpxRequest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.api.auth import create_access_token, Permission
from verlihub.models import HubListEntry, HubListEntryCreate, HubListEntryRead
from verlihub.models.database import Database
from verlihub.hublist import (
    HubListRegistrationClient,
    HubListStats,
    build_hub_info,
    prune_stale_hubs,
    _hubs_to_xml,
    _hubs_to_json,
    hublist_router,
    STALE_HUB_TIMEOUT,
    DEFAULT_REGISTRATION_INTERVAL,
)
from verlihub.config import VerlihubConfig, HubConfig, HubListConfig


# =============================================================================
# Helper: create a HubListEntry in the DB
# =============================================================================


async def _create_hub(
    session: AsyncSession,
    name: str = "Test Hub",
    address: str = "dchub://test.example.com:411",
    users: int = 42,
    share: int = 1_000_000,
    last_seen: datetime | None = None,
    **kwargs,
) -> HubListEntry:
    """Insert a HubListEntry and return it."""
    hub = HubListEntry(
        name=name,
        address=address,
        description=kwargs.get("description", "A test hub"),
        users=users,
        share=share,
        min_share=kwargs.get("min_share", 0),
        max_users=kwargs.get("max_users", 1000),
        country=kwargs.get("country", "US"),
        encoding=kwargs.get("encoding", "UTF-8"),
        owner=kwargs.get("owner", "admin"),
        email=kwargs.get("email", ""),
        website=kwargs.get("website", ""),
        logo=kwargs.get("logo", ""),
        status=kwargs.get("status", 1),
        software=kwargs.get("software", "Verlihub-py"),
        ip=kwargs.get("ip", ""),
        hostname=kwargs.get("hostname", ""),
        city=kwargs.get("city", ""),
        asn=kwargs.get("asn", ""),
        last_seen=last_seen or datetime.utcnow(),
        registered_at=kwargs.get("registered_at", datetime.utcnow()),
    )
    session.add(hub)
    await session.commit()
    await session.refresh(hub)
    return hub


# =============================================================================
# 1. HubListEntry Model CRUD
# =============================================================================


class TestHubListEntryModel:
    """Test the HubListEntry SQLModel (database layer)."""

    @pytest.mark.asyncio
    async def test_create_entry(self, db: Database, db_session: AsyncSession):
        """Create a hublist entry and verify it's persisted."""
        hub = await _create_hub(db_session, name="My Hub", address="dchub://my.hub:411")
        assert hub.id is not None
        assert hub.name == "My Hub"
        assert hub.address == "dchub://my.hub:411"

    @pytest.mark.asyncio
    async def test_read_entry(self, db: Database, db_session: AsyncSession):
        """Read a hublist entry by ID."""
        hub = await _create_hub(db_session)
        loaded = await db_session.get(HubListEntry, hub.id)
        assert loaded is not None
        assert loaded.name == hub.name
        assert loaded.address == hub.address

    @pytest.mark.asyncio
    async def test_update_entry(self, db: Database, db_session: AsyncSession):
        """Update fields on an existing entry."""
        hub = await _create_hub(db_session, users=10)
        hub.users = 100
        hub.share = 999_999
        session = db_session
        session.add(hub)
        await session.commit()
        await session.refresh(hub)
        assert hub.users == 100
        assert hub.share == 999_999

    @pytest.mark.asyncio
    async def test_delete_entry(self, db: Database, db_session: AsyncSession):
        """Delete a hublist entry."""
        hub = await _create_hub(db_session)
        hub_id = hub.id
        await db_session.delete(hub)
        await db_session.commit()
        assert await db_session.get(HubListEntry, hub_id) is None

    @pytest.mark.asyncio
    async def test_unique_addresses(self, db: Database, db_session: AsyncSession):
        """Multiple entries with different addresses can coexist."""
        h1 = await _create_hub(db_session, name="Hub A", address="dchub://a:411")
        h2 = await _create_hub(db_session, name="Hub B", address="dchub://b:411")
        result = await db_session.execute(select(HubListEntry))
        all_hubs = result.scalars().all()
        assert len(all_hubs) >= 2
        addresses = {h.address for h in all_hubs}
        assert "dchub://a:411" in addresses
        assert "dchub://b:411" in addresses

    @pytest.mark.asyncio
    async def test_default_field_values(self, db: Database, db_session: AsyncSession):
        """Default field values are applied correctly."""
        hub = HubListEntry(
            name="Minimal Hub",
            address="dchub://min:411",
            last_seen=datetime.utcnow(),
            registered_at=datetime.utcnow(),
        )
        db_session.add(hub)
        await db_session.commit()
        await db_session.refresh(hub)
        assert hub.users == 0
        assert hub.share == 0
        assert hub.encoding == "UTF-8"
        assert hub.status == 1

    @pytest.mark.asyncio
    async def test_entry_read_schema(self, db: Database, db_session: AsyncSession):
        """HubListEntryRead includes the id field."""
        hub = await _create_hub(db_session)
        read = HubListEntryRead.model_validate(hub)
        assert read.id == hub.id
        assert read.name == hub.name

    @pytest.mark.asyncio
    async def test_entry_create_schema_validation(self):
        """HubListEntryCreate validates required fields."""
        create = HubListEntryCreate(name="Test", address="dchub://test:411")
        assert create.name == "Test"
        assert create.users == 0  # default


# =============================================================================
# 2. XML / JSON serialization
# =============================================================================


class TestSerialization:
    """Test XML and JSON serialization of hub lists."""

    @pytest.mark.asyncio
    async def test_xml_output(self, db: Database, db_session: AsyncSession):
        """XML output follows the NMDC hublist format."""
        h1 = await _create_hub(db_session, name="Hub Alpha", address="dchub://alpha:411", users=10)
        h2 = await _create_hub(db_session, name="Hub Beta", address="dchub://beta:411", users=20)
        xml_str = _hubs_to_xml([h1, h2])

        assert xml_str.startswith('<?xml version="1.0"')
        root = ET.fromstring(xml_str)
        assert root.tag == "Hubs"
        hubs = root.findall("Hub")
        assert len(hubs) == 2
        names = {h.attrib["Name"] for h in hubs}
        assert "Hub Alpha" in names
        assert "Hub Beta" in names

    @pytest.mark.asyncio
    async def test_xml_attributes(self, db: Database, db_session: AsyncSession):
        """Each Hub element has the standard NMDC attributes."""
        hub = await _create_hub(
            db_session,
            name="Full Hub",
            address="dchub://full:411",
            users=55,
            share=123456,
            country="DE",
            encoding="CP1252",
            owner="boss",
        )
        xml_str = _hubs_to_xml([hub])
        root = ET.fromstring(xml_str)
        elem = root.find("Hub")
        assert elem.attrib["Name"] == "Full Hub"
        assert elem.attrib["Address"] == "dchub://full:411"
        assert elem.attrib["Users"] == "55"
        assert elem.attrib["Share"] == "123456"
        assert elem.attrib["Country"] == "DE"
        assert elem.attrib["Encoding"] == "CP1252"
        assert elem.attrib["Owner"] == "boss"

    @pytest.mark.asyncio
    async def test_json_output(self, db: Database, db_session: AsyncSession):
        """JSON output is a list of dicts with all hub fields."""
        hub = await _create_hub(db_session, name="JSON Hub", address="dchub://json:411", users=7)
        json_str = _hubs_to_json([hub])
        data = json.loads(json_str)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "JSON Hub"
        assert data[0]["users"] == 7
        assert data[0]["address"] == "dchub://json:411"
        assert "last_seen" in data[0]

    def test_empty_list_xml(self):
        """Empty hub list produces valid but empty XML."""
        xml_str = _hubs_to_xml([])
        root = ET.fromstring(xml_str)
        assert root.tag == "Hubs"
        assert len(root.findall("Hub")) == 0

    def test_empty_list_json(self):
        """Empty hub list produces an empty JSON array."""
        json_str = _hubs_to_json([])
        assert json.loads(json_str) == []


# =============================================================================
# 3. Stale Hub Pruning
# =============================================================================


class TestStaleHubPruning:
    """Test the background pruning of stale hub entries."""

    @pytest.mark.asyncio
    async def test_prune_stale_hubs(self, db: Database, db_session: AsyncSession):
        """Stale hubs (last_seen older than timeout) are marked offline (status=0)."""
        old_time = datetime.utcnow() - timedelta(seconds=STALE_HUB_TIMEOUT + 60)
        fresh_time = datetime.utcnow()

        stale_hub = await _create_hub(
            db_session, name="Stale Hub", address="dchub://stale:411", last_seen=old_time,
        )
        fresh_hub = await _create_hub(
            db_session, name="Fresh Hub", address="dchub://fresh:411", last_seen=fresh_time,
        )

        pruned = await prune_stale_hubs(timeout=STALE_HUB_TIMEOUT)
        assert pruned == 1

        # Verify through a fresh session so MySQL REPEATABLE READ gives us a
        # new snapshot that includes the prune function's committed changes.
        async with db._session_factory() as verify:
            result = await verify.execute(select(HubListEntry))
            remaining = result.scalars().all()
            assert len(remaining) == 2
            by_name = {h.name: h for h in remaining}
            assert by_name["Stale Hub"].status == 0
            assert by_name["Fresh Hub"].status == 1

    @pytest.mark.asyncio
    async def test_prune_with_no_stale_hubs(self, db: Database, db_session: AsyncSession):
        """No hubs are pruned when all are fresh."""
        await _create_hub(db_session, name="Fresh A", address="dchub://a:411")
        await _create_hub(db_session, name="Fresh B", address="dchub://b:411")

        pruned = await prune_stale_hubs(timeout=STALE_HUB_TIMEOUT)
        assert pruned == 0

    @pytest.mark.asyncio
    async def test_prune_all_stale(self, db: Database, db_session: AsyncSession):
        """All hubs are pruned when all are stale."""
        old = datetime.utcnow() - timedelta(seconds=STALE_HUB_TIMEOUT + 120)
        await _create_hub(db_session, name="Old A", address="dchub://a:411", last_seen=old)
        await _create_hub(db_session, name="Old B", address="dchub://b:411", last_seen=old)

        pruned = await prune_stale_hubs(timeout=STALE_HUB_TIMEOUT)
        assert pruned == 2

    @pytest.mark.asyncio
    async def test_prune_custom_timeout(self, db: Database, db_session: AsyncSession):
        """Custom timeout value is respected."""
        # 5 minutes ago
        recent = datetime.utcnow() - timedelta(seconds=300)
        await _create_hub(db_session, name="Recent", address="dchub://r:411", last_seen=recent)

        # 5-minute-old entry should survive 10-minute timeout
        pruned = await prune_stale_hubs(timeout=600)
        assert pruned == 0

        # But not survive 2-minute timeout
        pruned = await prune_stale_hubs(timeout=120)
        assert pruned == 1


# =============================================================================
# 4. HubListRegistrationClient (unit tests with httpx mocked)
# =============================================================================


class TestRegistrationClient:
    """Test the outbound hub registration client."""

    def test_default_interval(self):
        """Default registration interval is set correctly."""
        client = HubListRegistrationClient(servers=["hublist.example.com"])
        assert client.interval == DEFAULT_REGISTRATION_INTERVAL
        assert len(client.servers) == 1

    def test_custom_interval(self):
        """Custom interval is respected."""
        client = HubListRegistrationClient(servers=[], interval=120)
        assert client.interval == 120

    def test_empty_servers(self):
        """Client with no servers has empty list."""
        client = HubListRegistrationClient()
        assert client.servers == []

    def test_last_results_empty_initially(self):
        """last_results is empty before any registration round."""
        client = HubListRegistrationClient(servers=["s1", "s2"])
        assert client.last_results == {}

    @pytest.mark.asyncio
    async def test_register_one_success(self):
        """Successful registration to a single server."""
        _req = HttpxRequest("POST", "http://hublist.example.com")
        mock_response = HttpxResponse(200, text="OK", request=_req)
        with patch("verlihub.hublist.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            result = await HubListRegistrationClient._register_one(
                "hublist.example.com",
                {"name": "My Hub", "address": "dchub://my:411", "users": 5},
            )
            assert result == "OK"
            mock_instance.post.assert_called_once()
            call_args = mock_instance.post.call_args
            assert call_args[0][0] == "http://hublist.example.com"
            assert call_args[1]["data"]["Name"] == "My Hub"

    @pytest.mark.asyncio
    async def test_register_one_with_full_url(self):
        """Server with full URL is not double-prefixed."""
        _req = HttpxRequest("POST", "https://secure.hublist.net/register")
        mock_response = HttpxResponse(200, text="OK", request=_req)
        with patch("verlihub.hublist.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            await HubListRegistrationClient._register_one(
                "https://secure.hublist.net/register",
                {"name": "Hub", "address": "dchub://h:411"},
            )
            call_args = mock_instance.post.call_args
            assert call_args[0][0] == "https://secure.hublist.net/register"

    @pytest.mark.asyncio
    async def test_register_one_form_fields(self):
        """Registration POST includes standard NMDC form fields."""
        _req = HttpxRequest("POST", "http://hublist.test")
        mock_response = HttpxResponse(200, text="OK", request=_req)
        with patch("verlihub.hublist.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            info = {
                "name": "TestHub",
                "address": "dchub://test:411",
                "description": "A great hub",
                "users": 50,
                "share": 100000,
                "min_share": 0,
                "max_users": 200,
                "country": "DE",
                "encoding": "UTF-8",
                "owner": "admin",
                "website": "https://hub.example.com",
                "status": 1,
                "software": "Verlihub-py",
            }
            await HubListRegistrationClient._register_one("hublist.test", info)
            form_data = mock_instance.post.call_args[1]["data"]
            assert form_data["Name"] == "TestHub"
            assert form_data["Host"] == "dchub://test:411"
            assert form_data["Description"] == "A great hub"
            assert form_data["Users"] == "50"
            assert form_data["Share"] == "100000"
            assert form_data["Minshare"] == "0"
            assert form_data["Maxusers"] == "200"
            assert form_data["Country"] == "DE"
            assert form_data["Software"] == "Verlihub-py"

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        """Client starts and stops the background task cleanly."""
        client = HubListRegistrationClient(servers=["hublist.test"], interval=3600)
        info_fn = lambda: {"name": "Hub", "address": "dchub://h:411"}

        with patch.object(client, "_register_all", new_callable=AsyncMock):
            await client.start(info_fn)
            assert client._running is True
            assert client._task is not None

            await client.stop()
            assert client._running is False

    @pytest.mark.asyncio
    async def test_register_all_collects_results(self):
        """_register_all updates last_results for each server."""
        client = HubListRegistrationClient(servers=["s1.test", "s2.test"])

        with patch.object(
            HubListRegistrationClient,
            "_register_one",
            new_callable=AsyncMock,
            side_effect=["OK", "Registered"],
        ):
            await client._register_all({"name": "Hub", "address": "dchub://h:411"})
            assert client._last_results["s1.test"] == "OK"
            assert client._last_results["s2.test"] == "Registered"

    @pytest.mark.asyncio
    async def test_register_all_handles_failure(self):
        """_register_all records error strings for failed servers."""
        client = HubListRegistrationClient(servers=["good.test", "bad.test"])

        async def _side_effect(server, info):
            if server == "bad.test":
                raise ConnectionError("refused")
            return "OK"

        with patch.object(
            HubListRegistrationClient,
            "_register_one",
            new_callable=AsyncMock,
            side_effect=_side_effect,
        ):
            await client._register_all({"name": "Hub", "address": "dchub://h:411"})
            assert client._last_results["good.test"] == "OK"
            assert "error:" in client._last_results["bad.test"]


# =============================================================================
# 5. build_hub_info() helper
# =============================================================================


class TestBuildHubInfo:
    """Test the build_hub_info() helper that creates the registration dict."""

    def test_with_no_context(self):
        """Without hub context, returns defaults from Python config."""
        info = build_hub_info(ctx=None)
        assert info["software"] == "Verlihub-py"
        assert info["status"] == 1
        assert "name" in info or "software" in info

    def test_with_mock_context(self):
        """With a mock hub context, reads values from C++ config."""
        mock_ctx = MagicMock()
        mock_ctx.get_config.side_effect = lambda section, key, default="": {
            "hub_name": "Mock Hub",
            "hub_host": "mock.example.com",
            "listen_port": "411",
            "hub_desc": "Mock description",
            "min_share": "1024",
            "max_users": "500",
            "hub_encoding": "CP1252",
            "hub_owner": "mockowner",
        }.get(key, default)
        mock_ctx.user_count = 33
        mock_ctx.total_share = 999

        info = build_hub_info(ctx=mock_ctx)
        assert info["name"] == "Mock Hub"
        assert info["users"] == 33
        assert info["share"] == 999
        assert info["min_share"] == 1024
        assert info["max_users"] == 500
        assert info["encoding"] == "CP1252"
        assert info["owner"] == "mockowner"

    def test_fallback_on_context_error(self):
        """If context raises, falls back to Python config."""
        mock_ctx = MagicMock()
        mock_ctx.get_config.side_effect = RuntimeError("SWIG crash")

        info = build_hub_info(ctx=mock_ctx)
        # Should still return a valid dict (from config fallback or defaults)
        assert info["software"] == "Verlihub-py"
        assert info["status"] == 1


# =============================================================================
# 6. HubListConfig dataclass
# =============================================================================


class TestHubListConfig:
    """Test the HubListConfig dataclass and YAML round-trip."""

    def test_defaults(self):
        """Default values are sensible."""
        cfg = HubListConfig()
        assert cfg.server_enabled is False
        assert cfg.registration_interval == 600
        assert cfg.stale_timeout == 1800

    def test_custom_values(self):
        """Custom values are stored correctly."""
        cfg = HubListConfig(server_enabled=True, registration_interval=120, stale_timeout=900)
        assert cfg.server_enabled is True
        assert cfg.registration_interval == 120
        assert cfg.stale_timeout == 900

    def test_in_verlihub_config(self):
        """HubListConfig is part of VerlihubConfig."""
        cfg = VerlihubConfig()
        assert hasattr(cfg, "hublist")
        assert isinstance(cfg.hublist, HubListConfig)
        assert cfg.hublist.server_enabled is False

    def test_from_dict(self):
        """HubListConfig is loaded from dict via VerlihubConfig.from_dict."""
        data = {
            "hublist": {
                "server_enabled": True,
                "registration_interval": 300,
                "stale_timeout": 600,
            }
        }
        cfg = VerlihubConfig.from_dict(data)
        assert cfg.hublist.server_enabled is True
        assert cfg.hublist.registration_interval == 300
        assert cfg.hublist.stale_timeout == 600

    def test_from_dict_partial(self):
        """Partial hublist config uses defaults for missing fields."""
        data = {"hublist": {"server_enabled": True}}
        cfg = VerlihubConfig.from_dict(data)
        assert cfg.hublist.server_enabled is True
        assert cfg.hublist.registration_interval == 600  # default
        assert cfg.hublist.stale_timeout == 1800  # default

    def test_hublist_servers_in_hub_config(self):
        """hublist_servers list is part of HubConfig."""
        cfg = HubConfig()
        assert isinstance(cfg.hublist_servers, list)
        assert len(cfg.hublist_servers) == 2
        assert "hublist.te-home.net" in cfg.hublist_servers

    def test_hublist_servers_from_dict(self):
        """hublist_servers loaded correctly from YAML dict."""
        data = {
            "hub": {
                "hublist_servers": ["my-hublist.org", "another.net"],
            }
        }
        cfg = VerlihubConfig.from_dict(data)
        assert cfg.hub.hublist_servers == ["my-hublist.org", "another.net"]


# =============================================================================
# 7. FastAPI endpoint tests (using TestClient)
# =============================================================================


@pytest_asyncio.fixture
async def hublist_app(db: Database):
    """Create a test FastAPI app with the hublist router mounted."""
    from fastapi import FastAPI
    from verlihub.hublist import hublist_router

    app = FastAPI()
    app.include_router(hublist_router, prefix="/hublist")
    return app


@pytest_asyncio.fixture
async def hublist_client(hublist_app) -> AsyncGenerator[AsyncClient, None]:
    """HTTPX AsyncClient against the hublist test app."""
    from httpx import ASGITransport

    transport = ASGITransport(app=hublist_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


class TestHubListEndpoints:
    """Test the hublist FastAPI endpoints."""

    @pytest.mark.asyncio
    async def test_get_empty_hublist_xml(self, hublist_client: AsyncClient):
        """GET / returns empty XML hublist when no hubs registered."""
        resp = await hublist_client.get("/hublist/")
        assert resp.status_code == 200
        assert "text/xml" in resp.headers["content-type"]
        root = ET.fromstring(resp.text)
        assert root.tag == "Hubs"
        assert len(root.findall("Hub")) == 0

    @pytest.mark.asyncio
    async def test_get_empty_hublist_json(self, hublist_client: AsyncClient):
        """GET /?fmt=json returns empty JSON array."""
        resp = await hublist_client.get("/hublist/?fmt=json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]
        data = resp.json()
        assert data == []

    @pytest.mark.asyncio
    async def test_register_hub_json(self, hublist_client: AsyncClient):
        """POST /register with JSON body creates a hub entry."""
        payload = {
            "name": "JSON Hub",
            "address": "dchub://json.hub:411",
            "description": "Registered via JSON",
            "users": 10,
            "share": 50000,
        }
        resp = await hublist_client.post(
            "/hublist/register",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "OK"
        assert body["name"] == "JSON Hub"
        assert body["address"] == "dchub://json.hub:411"
        assert body["id"] is not None

    @pytest.mark.asyncio
    async def test_register_hub_form(self, hublist_client: AsyncClient):
        """POST /register with form-encoded data (NMDC standard) creates a hub entry."""
        form_data = {
            "Name": "Form Hub",
            "Host": "dchub://form.hub:411",
            "Description": "Registered via form",
            "Users": "20",
            "Share": "100000",
            "Country": "DE",
            "Software": "Verlihub 1.5",
        }
        resp = await hublist_client.post(
            "/hublist/register",
            data=form_data,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "OK"
        assert body["name"] == "Form Hub"

    @pytest.mark.asyncio
    async def test_register_upsert_same_address(self, hublist_client: AsyncClient):
        """Registering the same address twice updates the existing entry."""
        payload1 = {
            "name": "Hub v1",
            "address": "dchub://upsert.hub:411",
            "users": 5,
        }
        resp1 = await hublist_client.post(
            "/hublist/register",
            json=payload1,
            headers={"Content-Type": "application/json"},
        )
        hub_id_1 = resp1.json()["id"]

        payload2 = {
            "name": "Hub v2",
            "address": "dchub://upsert.hub:411",
            "users": 50,
        }
        resp2 = await hublist_client.post(
            "/hublist/register",
            json=payload2,
            headers={"Content-Type": "application/json"},
        )
        hub_id_2 = resp2.json()["id"]

        # Same entry updated, not a new one
        assert hub_id_1 == hub_id_2
        assert resp2.json()["name"] == "Hub v2"

    @pytest.mark.asyncio
    async def test_register_different_addresses_create_separate(self, hublist_client: AsyncClient):
        """Different addresses create separate entries."""
        await hublist_client.post(
            "/hublist/register",
            json={"name": "Hub A", "address": "dchub://a:411"},
            headers={"Content-Type": "application/json"},
        )
        await hublist_client.post(
            "/hublist/register",
            json={"name": "Hub B", "address": "dchub://b:411"},
            headers={"Content-Type": "application/json"},
        )

        resp = await hublist_client.get("/hublist/?fmt=json")
        data = resp.json()
        assert len(data) == 2
        names = {h["name"] for h in data}
        assert "Hub A" in names
        assert "Hub B" in names

    @pytest.mark.asyncio
    async def test_register_missing_address(self, hublist_client: AsyncClient):
        """POST /register without address returns 400."""
        resp = await hublist_client.post(
            "/hublist/register",
            json={"name": "No Address Hub"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "address" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_register_missing_name(self, hublist_client: AsyncClient):
        """POST /register without name returns 400."""
        resp = await hublist_client.post(
            "/hublist/register",
            json={"address": "dchub://noname:411"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "name" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_stats_endpoint_empty(self, hublist_client: AsyncClient):
        """GET /stats returns zeros when no hubs registered."""
        resp = await hublist_client.get("/hublist/stats")
        assert resp.status_code == 200
        stats = resp.json()
        assert stats["total_hubs"] == 0
        assert stats["total_users"] == 0
        assert stats["total_share"] == 0

    @pytest.mark.asyncio
    async def test_stats_endpoint_with_hubs(self, hublist_client: AsyncClient):
        """GET /stats aggregates users and share from registered hubs."""
        await hublist_client.post(
            "/hublist/register",
            json={"name": "Hub A", "address": "dchub://a:411", "users": 10, "share": 1000},
            headers={"Content-Type": "application/json"},
        )
        await hublist_client.post(
            "/hublist/register",
            json={"name": "Hub B", "address": "dchub://b:411", "users": 20, "share": 3000},
            headers={"Content-Type": "application/json"},
        )

        resp = await hublist_client.get("/hublist/stats")
        stats = resp.json()
        assert stats["total_hubs"] == 2
        assert stats["total_users"] == 30
        assert stats["total_share"] == 4000

    @pytest.mark.asyncio
    async def test_delete_hub_entry(self, hublist_client: AsyncClient):
        """DELETE /{hub_id} removes the entry (requires master auth)."""
        reg_resp = await hublist_client.post(
            "/hublist/register",
            json={"name": "Deletable", "address": "dchub://del:411"},
            headers={"Content-Type": "application/json"},
        )
        hub_id = reg_resp.json()["id"]

        token = create_access_token("master", Permission.MASTER)
        del_resp = await hublist_client.delete(
            f"/hublist/{hub_id}",
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted"] == hub_id

        # Verify it's gone
        stats_resp = await hublist_client.get("/hublist/stats")
        assert stats_resp.json()["total_hubs"] == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_hub(self, hublist_client: AsyncClient):
        """DELETE for a non-existent ID returns 404."""
        token = create_access_token("master", Permission.MASTER)
        resp = await hublist_client.delete(
            "/hublist/99999",
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_xml_format_after_registration(self, hublist_client: AsyncClient):
        """GET / returns XML with registered hubs."""
        await hublist_client.post(
            "/hublist/register",
            json={"name": "XML Test Hub", "address": "dchub://xml:411", "users": 7},
            headers={"Content-Type": "application/json"},
        )

        resp = await hublist_client.get("/hublist/")
        root = ET.fromstring(resp.text)
        hubs = root.findall("Hub")
        assert len(hubs) == 1
        assert hubs[0].attrib["Name"] == "XML Test Hub"
        assert hubs[0].attrib["Users"] == "7"

    @pytest.mark.asyncio
    async def test_json_format_after_registration(self, hublist_client: AsyncClient):
        """GET /?fmt=json returns JSON with registered hubs."""
        await hublist_client.post(
            "/hublist/register",
            json={"name": "JSON Test Hub", "address": "dchub://json:411", "share": 12345},
            headers={"Content-Type": "application/json"},
        )

        resp = await hublist_client.get("/hublist/?fmt=json")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "JSON Test Hub"
        assert data[0]["share"] == 12345


# =============================================================================
# 8. HubConfigUpdate API fields
# =============================================================================


class TestHubConfigUpdateFields:
    """Test that hublist_servers and hublist_server_enabled are accepted by the config API model."""

    def test_hublist_servers_field_exists(self):
        """HubConfigUpdate has hublist_servers field."""
        from verlihub.api.routes.hub import HubConfigUpdate
        update = HubConfigUpdate(hublist_servers=["hublist.test.com", "another.test"])
        assert update.hublist_servers == ["hublist.test.com", "another.test"]

    def test_hublist_server_enabled_field_exists(self):
        """HubConfigUpdate has hublist_server_enabled field."""
        from verlihub.api.routes.hub import HubConfigUpdate
        update = HubConfigUpdate(hublist_server_enabled=True)
        assert update.hublist_server_enabled is True

    def test_fields_default_to_none(self):
        """Hublist fields default to None (optional)."""
        from verlihub.api.routes.hub import HubConfigUpdate
        update = HubConfigUpdate()
        assert update.hublist_servers is None
        assert update.hublist_server_enabled is None


# =============================================================================
# 9. Router registration
# =============================================================================


class TestRouterRegistration:
    """Verify the hublist router is registered in the API."""

    def test_hublist_router_in_api(self):
        """hublist_router is included in the main api_router."""
        from verlihub.api import api_router
        route_paths = [r.path for r in api_router.routes if hasattr(r, "path")]
        # The hublist endpoints should be under /api/v1/hublist
        hublist_paths = [p for p in route_paths if "hublist" in p]
        assert len(hublist_paths) > 0, "hublist routes not registered in api_router"

    def test_hublist_endpoints_exist(self):
        """All expected hublist endpoints are registered."""
        from verlihub.api import api_router
        route_paths = set()
        for route in api_router.routes:
            if hasattr(route, "path"):
                route_paths.add(route.path)
            if hasattr(route, "routes"):
                for sub in route.routes:
                    if hasattr(sub, "path"):
                        route_paths.add(sub.path)
        # Check key endpoints exist (they may be nested)
        all_paths = " ".join(route_paths)
        assert "hublist" in all_paths


# =============================================================================
# 10. Constants
# =============================================================================


class TestConstants:
    """Verify module-level constants are sensible."""

    def test_default_interval(self):
        assert DEFAULT_REGISTRATION_INTERVAL == 600

    def test_stale_timeout(self):
        assert STALE_HUB_TIMEOUT == 1800

    def test_stale_timeout_gt_interval(self):
        """Stale timeout should be larger than registration interval."""
        assert STALE_HUB_TIMEOUT > DEFAULT_REGISTRATION_INTERVAL
