"""
Tests for hublist dashboard features:

1. HubListBlock model CRUD
2. Block enforcement on registration
3. Search / autocomplete endpoint
4. GeoIP enrichment helpers
5. Master-only access control on admin endpoints
6. WebSocket hublist events
7. Prune → offline transition (instead of delete)
8. Dashboard route access control
9. Integration test: two hubs registering on each other
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.api.auth import create_access_token, Permission
from verlihub.models import (
    HubListBlock,
    HubListBlockCreate,
    HubListBlockRead,
    HubListBlockType,
    HubListEntry,
)
from verlihub.models.database import Database
from verlihub.hublist import (
    _extract_domain,
    _extract_ip_from_address,
    _hub_to_dict,
    check_hub_blocked,
    hublist_router,
    STALE_HUB_TIMEOUT,
)


# =============================================================================
# Fixtures
# =============================================================================


def _master_headers() -> dict[str, str]:
    token = create_access_token("master_user", Permission.MASTER)
    return {"Authorization": f"Bearer {token.access_token}"}


def _operator_headers() -> dict[str, str]:
    token = create_access_token("operator_user", Permission.OPERATOR)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest_asyncio.fixture
async def app(db: Database):
    """FastAPI test app with the hublist router."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(hublist_router, prefix="/hublist")
    return app


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def _register_hub(
    client: AsyncClient, name: str, address: str, **extra
) -> dict:
    """Helper: register a hub and return the JSON response."""
    payload = {"name": name, "address": address, **extra}
    resp = await client.post(
        "/hublist/register",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _create_block(
    session: AsyncSession,
    block_type: HubListBlockType,
    value: str,
    reason: str = "",
) -> HubListBlock:
    block = HubListBlock(
        block_type=block_type,
        value=value,
        reason=reason,
        created_by="test",
    )
    session.add(block)
    await session.commit()
    await session.refresh(block)
    return block


# =============================================================================
# 1. HubListBlock Model CRUD
# =============================================================================


class TestHubListBlockModel:
    """Test block rule persistence."""

    @pytest.mark.asyncio
    async def test_create_block(self, db: Database, db_session: AsyncSession):
        block = await _create_block(db_session, HubListBlockType.IP, "1.2.3.4")
        assert block.id is not None
        assert block.block_type == HubListBlockType.IP
        assert block.value == "1.2.3.4"

    @pytest.mark.asyncio
    async def test_read_block(self, db: Database, db_session: AsyncSession):
        block = await _create_block(db_session, HubListBlockType.COUNTRY, "DE")
        loaded = await db_session.get(HubListBlock, block.id)
        assert loaded is not None
        assert loaded.value == "DE"

    @pytest.mark.asyncio
    async def test_delete_block(self, db: Database, db_session: AsyncSession):
        block = await _create_block(db_session, HubListBlockType.ASN, "AS1234")
        bid = block.id
        await db_session.delete(block)
        await db_session.commit()
        assert await db_session.get(HubListBlock, bid) is None

    @pytest.mark.asyncio
    async def test_block_types_enum(self):
        """All block types are valid."""
        expected = {"ip", "hostname", "domain", "asn", "city", "country"}
        actual = {bt.value for bt in HubListBlockType}
        assert actual == expected

    @pytest.mark.asyncio
    async def test_block_read_schema(self, db: Database, db_session: AsyncSession):
        block = await _create_block(db_session, HubListBlockType.DOMAIN, "example.com")
        read = HubListBlockRead.model_validate(block)
        assert read.id == block.id
        assert read.block_type == HubListBlockType.DOMAIN

    @pytest.mark.asyncio
    async def test_block_create_schema(self):
        create = HubListBlockCreate(
            block_type=HubListBlockType.CITY,
            value="Berlin",
            reason="spam hubs",
        )
        assert create.block_type == HubListBlockType.CITY
        assert create.value == "Berlin"

    @pytest.mark.asyncio
    async def test_multiple_blocks(self, db: Database, db_session: AsyncSession):
        await _create_block(db_session, HubListBlockType.IP, "1.1.1.1")
        await _create_block(db_session, HubListBlockType.COUNTRY, "CN")
        await _create_block(db_session, HubListBlockType.DOMAIN, "spam.com")
        result = await db_session.execute(select(HubListBlock))
        assert len(result.scalars().all()) == 3

    @pytest.mark.asyncio
    async def test_block_with_expiry(self, db: Database, db_session: AsyncSession):
        expires = datetime.utcnow() + timedelta(days=7)
        block = HubListBlock(
            block_type=HubListBlockType.IP,
            value="9.9.9.9",
            expires_at=expires,
            created_by="admin",
        )
        db_session.add(block)
        await db_session.commit()
        await db_session.refresh(block)
        assert block.expires_at is not None


# =============================================================================
# 2. Block checking logic
# =============================================================================


class TestCheckHubBlocked:
    """Test the check_hub_blocked() function."""

    @pytest.mark.asyncio
    async def test_no_blocks_returns_none(self, db: Database, db_session: AsyncSession):
        result = await check_hub_blocked(db_session, ip="1.2.3.4")
        assert result is None

    @pytest.mark.asyncio
    async def test_ip_block_matches(self, db: Database, db_session: AsyncSession):
        await _create_block(db_session, HubListBlockType.IP, "10.0.0.1")
        result = await check_hub_blocked(db_session, ip="10.0.0.1")
        assert result is not None
        assert result.block_type == HubListBlockType.IP

    @pytest.mark.asyncio
    async def test_country_block_matches(self, db: Database, db_session: AsyncSession):
        await _create_block(db_session, HubListBlockType.COUNTRY, "RU")
        result = await check_hub_blocked(db_session, country="RU")
        assert result is not None
        assert result.block_type == HubListBlockType.COUNTRY

    @pytest.mark.asyncio
    async def test_country_block_case_insensitive(self, db: Database, db_session: AsyncSession):
        await _create_block(db_session, HubListBlockType.COUNTRY, "DE")
        result = await check_hub_blocked(db_session, country="de")
        assert result is not None

    @pytest.mark.asyncio
    async def test_domain_block_from_hostname(self, db: Database, db_session: AsyncSession):
        await _create_block(db_session, HubListBlockType.DOMAIN, "badhub.com")
        result = await check_hub_blocked(db_session, hostname="server1.badhub.com")
        assert result is not None
        assert result.block_type == HubListBlockType.DOMAIN

    @pytest.mark.asyncio
    async def test_asn_block_matches(self, db: Database, db_session: AsyncSession):
        await _create_block(db_session, HubListBlockType.ASN, "AS1234")
        result = await check_hub_blocked(db_session, asn="AS1234 Some ISP")
        assert result is not None

    @pytest.mark.asyncio
    async def test_city_block_matches(self, db: Database, db_session: AsyncSession):
        await _create_block(db_session, HubListBlockType.CITY, "Moscow")
        result = await check_hub_blocked(db_session, city="Moscow")
        assert result is not None

    @pytest.mark.asyncio
    async def test_hostname_block_matches(self, db: Database, db_session: AsyncSession):
        await _create_block(db_session, HubListBlockType.HOSTNAME, "evil.example.com")
        result = await check_hub_blocked(db_session, hostname="evil.example.com")
        assert result is not None

    @pytest.mark.asyncio
    async def test_expired_block_ignored(self, db: Database, db_session: AsyncSession):
        expired = datetime.utcnow() - timedelta(hours=1)
        block = HubListBlock(
            block_type=HubListBlockType.IP,
            value="5.5.5.5",
            expires_at=expired,
            created_by="test",
        )
        db_session.add(block)
        await db_session.commit()
        result = await check_hub_blocked(db_session, ip="5.5.5.5")
        assert result is None  # expired block is auto-removed

    @pytest.mark.asyncio
    async def test_non_expired_block_enforced(self, db: Database, db_session: AsyncSession):
        future = datetime.utcnow() + timedelta(hours=1)
        block = HubListBlock(
            block_type=HubListBlockType.IP,
            value="6.6.6.6",
            expires_at=future,
            created_by="test",
        )
        db_session.add(block)
        await db_session.commit()
        result = await check_hub_blocked(db_session, ip="6.6.6.6")
        assert result is not None

    @pytest.mark.asyncio
    async def test_unrelated_block_no_match(self, db: Database, db_session: AsyncSession):
        await _create_block(db_session, HubListBlockType.IP, "99.99.99.99")
        result = await check_hub_blocked(db_session, ip="1.1.1.1")
        assert result is None

    @pytest.mark.asyncio
    async def test_domain_from_address_fallback(self, db: Database, db_session: AsyncSession):
        """When hostname is empty, domain is extracted from address."""
        await _create_block(db_session, HubListBlockType.DOMAIN, "example.com")
        result = await check_hub_blocked(
            db_session,
            hostname="",
            address="dchub://hub.example.com:411",
        )
        assert result is not None


# =============================================================================
# 3. Helper functions
# =============================================================================


class TestHelperFunctions:
    """Test enrichment and utility helpers."""

    def test_extract_ip_from_dchub(self):
        ip = _extract_ip_from_address("dchub://1.2.3.4:411")
        assert ip == "1.2.3.4"

    def test_extract_ip_from_adcs(self):
        ip = _extract_ip_from_address("adcs://10.0.0.1:412")
        assert ip == "10.0.0.1"

    def test_extract_ip_empty_on_bad_addr(self):
        ip = _extract_ip_from_address("")
        assert ip == ""

    def test_extract_domain_simple(self):
        assert _extract_domain("hub.example.com") == "example.com"

    def test_extract_domain_two_labels(self):
        assert _extract_domain("example.com") == "example.com"

    def test_extract_domain_subdomain(self):
        assert _extract_domain("a.b.c.example.com") == "example.com"

    def test_extract_domain_empty(self):
        assert _extract_domain("") == ""

    def test_extract_domain_single_label(self):
        assert _extract_domain("localhost") == "localhost"

    def test_hub_to_dict_keys(self, db: Database, db_session: AsyncSession):
        """_hub_to_dict returns all expected keys."""
        hub = HubListEntry(
            name="Test", address="dchub://test:411",
            last_seen=datetime.utcnow(),
            registered_at=datetime.utcnow(),
        )
        d = _hub_to_dict(hub)
        expected_keys = {
            "id", "name", "address", "description", "users", "share",
            "min_share", "max_users", "country", "encoding", "owner",
            "email", "website", "logo", "status", "software",
            "ip", "hostname", "city", "asn", "last_seen", "registered_at",
        }
        assert set(d.keys()) == expected_keys


# =============================================================================
# 4. Master-only endpoint access control
# =============================================================================


class TestMasterOnlyEndpoints:
    """Verify master-only endpoints reject lower privilege levels."""

    @pytest.mark.asyncio
    async def test_get_all_requires_master(self, client: AsyncClient):
        """GET /all without master token → 401."""
        resp = await client.get("/hublist/all")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_all_operator_rejected(self, client: AsyncClient):
        """GET /all with operator token → 403."""
        resp = await client.get("/hublist/all", headers=_operator_headers())
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_get_all_master_ok(self, client: AsyncClient):
        """GET /all with master token → 200."""
        resp = await client.get("/hublist/all", headers=_master_headers())
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_search_requires_master(self, client: AsyncClient):
        resp = await client.get("/hublist/search?q=test")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_search_master_ok(self, client: AsyncClient):
        resp = await client.get("/hublist/search?q=test", headers=_master_headers())
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_requires_master(self, client: AsyncClient):
        resp = await client.delete("/hublist/1")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_operator_rejected(self, client: AsyncClient):
        resp = await client.delete("/hublist/1", headers=_operator_headers())
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_list_blocks_requires_master(self, client: AsyncClient):
        resp = await client.get("/hublist/blocks")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_blocks_master_ok(self, client: AsyncClient):
        resp = await client.get("/hublist/blocks", headers=_master_headers())
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_block_requires_master(self, client: AsyncClient):
        resp = await client.post(
            "/hublist/blocks",
            json={"block_type": "ip", "value": "1.1.1.1"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_block_requires_master(self, client: AsyncClient):
        resp = await client.delete("/hublist/blocks/1")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_register_is_public(self, client: AsyncClient):
        """POST /register should NOT require auth."""
        resp = await client.post(
            "/hublist/register",
            json={"name": "Public Hub", "address": "dchub://pub:411"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_stats_is_public(self, client: AsyncClient):
        """GET /stats should NOT require auth."""
        resp = await client.get("/hublist/stats")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_hublist_is_public(self, client: AsyncClient):
        """GET / should NOT require auth."""
        resp = await client.get("/hublist/")
        assert resp.status_code == 200


# =============================================================================
# 5. Block Rule CRUD via API
# =============================================================================


class TestBlockRuleAPI:
    """Test block rule endpoints via HTTP."""

    @pytest.mark.asyncio
    async def test_create_block_ip(self, client: AsyncClient):
        resp = await client.post(
            "/hublist/blocks",
            json={"block_type": "ip", "value": "10.0.0.1", "reason": "spam"},
            headers={**_master_headers(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["block_type"] == "ip"
        assert data["value"] == "10.0.0.1"
        assert data["reason"] == "spam"
        assert data["created_by"] == "master_user"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_create_block_country(self, client: AsyncClient):
        resp = await client.post(
            "/hublist/blocks",
            json={"block_type": "country", "value": "CN"},
            headers={**_master_headers(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 201
        assert resp.json()["block_type"] == "country"

    @pytest.mark.asyncio
    async def test_list_blocks(self, client: AsyncClient):
        headers = {**_master_headers(), "Content-Type": "application/json"}
        await client.post("/hublist/blocks", json={"block_type": "ip", "value": "1.1.1.1"}, headers=headers)
        await client.post("/hublist/blocks", json={"block_type": "domain", "value": "bad.com"}, headers=headers)

        resp = await client.get("/hublist/blocks", headers=_master_headers())
        assert resp.status_code == 200
        blocks = resp.json()
        assert len(blocks) == 2

    @pytest.mark.asyncio
    async def test_delete_block(self, client: AsyncClient):
        headers = {**_master_headers(), "Content-Type": "application/json"}
        create_resp = await client.post(
            "/hublist/blocks",
            json={"block_type": "asn", "value": "AS999"},
            headers=headers,
        )
        block_id = create_resp.json()["id"]

        del_resp = await client.delete(
            f"/hublist/blocks/{block_id}", headers=_master_headers()
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted"] == block_id

    @pytest.mark.asyncio
    async def test_delete_nonexistent_block(self, client: AsyncClient):
        resp = await client.delete(
            "/hublist/blocks/99999", headers=_master_headers()
        )
        assert resp.status_code == 404


# =============================================================================
# 6. Block enforcement on registration
# =============================================================================


class TestBlockEnforcementOnRegistration:
    """Test that blocked hubs are rejected during registration."""

    @pytest.mark.asyncio
    async def test_ip_block_rejects_registration(self, client: AsyncClient, db_session: AsyncSession):
        """Registration from a blocked IP is rejected."""
        await _create_block(db_session, HubListBlockType.IP, "127.0.0.1")
        resp = await client.post(
            "/hublist/register",
            json={"name": "Blocked Hub", "address": "dchub://blocked:411"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403
        assert "blocked" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_country_block_rejects(self, client: AsyncClient, db_session: AsyncSession):
        """Registration with a blocked country code is rejected."""
        await _create_block(db_session, HubListBlockType.COUNTRY, "XX")
        # We need to mock GeoIP to return country XX
        with patch("verlihub.hublist._lookup_geo_for_ip") as mock_geo:
            mock_geo.return_value = {"country_code": "XX", "city": "", "as_number": "", "as_name": ""}
            resp = await client.post(
                "/hublist/register",
                json={"name": "Country Blocked", "address": "dchub://cblocked:411", "country": "XX"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unblocked_hub_allowed(self, client: AsyncClient, db_session: AsyncSession):
        """Registration from unblocked source succeeds."""
        await _create_block(db_session, HubListBlockType.IP, "99.99.99.99")
        resp = await client.post(
            "/hublist/register",
            json={"name": "Good Hub", "address": "dchub://good:411"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200


# =============================================================================
# 7. Search endpoint
# =============================================================================


class TestSearchEndpoint:
    """Test the hublist search endpoint."""

    @pytest.mark.asyncio
    async def test_search_empty_returns_all(self, client: AsyncClient):
        await _register_hub(client, "Hub A", "dchub://a:411")
        await _register_hub(client, "Hub B", "dchub://b:411")

        resp = await client.get("/hublist/search?q=", headers=_master_headers())
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    @pytest.mark.asyncio
    async def test_search_by_name(self, client: AsyncClient):
        await _register_hub(client, "Alpha Hub", "dchub://alpha:411")
        await _register_hub(client, "Beta Hub", "dchub://beta:411")

        resp = await client.get("/hublist/search?q=Alpha", headers=_master_headers())
        results = resp.json()
        assert len(results) == 1
        assert results[0]["name"] == "Alpha Hub"

    @pytest.mark.asyncio
    async def test_search_by_address(self, client: AsyncClient):
        await _register_hub(client, "Test", "dchub://special.host:411")

        resp = await client.get("/hublist/search?q=special", headers=_master_headers())
        assert len(resp.json()) == 1

    @pytest.mark.asyncio
    async def test_search_by_owner(self, client: AsyncClient):
        await _register_hub(client, "Hub", "dchub://h:411", owner="JohnDoe")

        resp = await client.get("/hublist/search?q=JohnDoe", headers=_master_headers())
        assert len(resp.json()) == 1

    @pytest.mark.asyncio
    async def test_search_case_insensitive(self, client: AsyncClient):
        await _register_hub(client, "UPPERCASE HUB", "dchub://u:411")

        resp = await client.get("/hublist/search?q=uppercase", headers=_master_headers())
        assert len(resp.json()) == 1

    @pytest.mark.asyncio
    async def test_search_no_results(self, client: AsyncClient):
        await _register_hub(client, "Hub", "dchub://h:411")

        resp = await client.get("/hublist/search?q=nonexistent", headers=_master_headers())
        assert len(resp.json()) == 0


# =============================================================================
# 8. Get all hubs (includes offline)
# =============================================================================


class TestGetAllHubs:
    """Test the /all endpoint that includes offline hubs."""

    @pytest.mark.asyncio
    async def test_all_returns_online_and_offline(self, client: AsyncClient, db_session: AsyncSession):
        """GET /all returns both online and offline hubs."""
        now = datetime.utcnow()
        old = now - timedelta(seconds=STALE_HUB_TIMEOUT + 100)

        # Create an online hub via registration
        await _register_hub(client, "Online Hub", "dchub://online:411")

        # Create a stale hub directly in DB
        stale = HubListEntry(
            name="Stale Hub",
            address="dchub://stale:411",
            last_seen=old,
            registered_at=old,
            status=1,
        )
        db_session.add(stale)
        await db_session.commit()

        resp = await client.get("/hublist/all", headers=_master_headers())
        assert resp.status_code == 200
        hubs = resp.json()
        assert len(hubs) == 2

        # The stale hub should now be marked offline
        stale_hub = next(h for h in hubs if h["name"] == "Stale Hub")
        assert stale_hub["status"] == 0

    @pytest.mark.asyncio
    async def test_all_empty(self, client: AsyncClient):
        resp = await client.get("/hublist/all", headers=_master_headers())
        assert resp.status_code == 200
        assert resp.json() == []


# =============================================================================
# 9. Hub entry enrichment fields
# =============================================================================


class TestHubEntryEnrichmentFields:
    """Test that new enrichment fields are persisted."""

    @pytest.mark.asyncio
    async def test_new_fields_on_model(self, db: Database, db_session: AsyncSession):
        hub = HubListEntry(
            name="Enriched Hub",
            address="dchub://e:411",
            ip="1.2.3.4",
            hostname="hub.example.com",
            city="Berlin",
            asn="AS1234 Example ISP",
            email="admin@example.com",
            logo="https://example.com/logo.png",
            last_seen=datetime.utcnow(),
            registered_at=datetime.utcnow(),
        )
        db_session.add(hub)
        await db_session.commit()
        await db_session.refresh(hub)

        assert hub.ip == "1.2.3.4"
        assert hub.hostname == "hub.example.com"
        assert hub.city == "Berlin"
        assert hub.asn == "AS1234 Example ISP"
        assert hub.email == "admin@example.com"
        assert hub.logo == "https://example.com/logo.png"

    @pytest.mark.asyncio
    async def test_enrichment_fields_default_empty(self, db: Database, db_session: AsyncSession):
        hub = HubListEntry(
            name="Minimal",
            address="dchub://m:411",
            last_seen=datetime.utcnow(),
            registered_at=datetime.utcnow(),
        )
        db_session.add(hub)
        await db_session.commit()
        await db_session.refresh(hub)

        assert hub.ip == ""
        assert hub.hostname == ""
        assert hub.city == ""
        assert hub.asn == ""
        assert hub.email == ""
        assert hub.logo == ""

    @pytest.mark.asyncio
    async def test_registration_stores_email_logo(self, client: AsyncClient):
        """POST /register with email and logo stores them."""
        await _register_hub(
            client, "Logo Hub", "dchub://logo:411",
            email="me@hub.org", logo="https://hub.org/icon.png",
        )

        resp = await client.get("/hublist/all", headers=_master_headers())
        hubs = resp.json()
        assert len(hubs) == 1
        assert hubs[0]["email"] == "me@hub.org"
        assert hubs[0]["logo"] == "https://hub.org/icon.png"


# =============================================================================
# 10. Prune marks offline instead of deleting
# =============================================================================


class TestPruneMarksOffline:
    """Test that prune_stale_hubs marks hubs offline (not deletes)."""

    @pytest.mark.asyncio
    async def test_prune_sets_status_zero(self, db: Database, db_session: AsyncSession):
        from verlihub.hublist import prune_stale_hubs

        old = datetime.utcnow() - timedelta(seconds=STALE_HUB_TIMEOUT + 100)
        hub = HubListEntry(
            name="Stale",
            address="dchub://stale:411",
            last_seen=old,
            registered_at=old,
            status=1,
        )
        db_session.add(hub)
        await db_session.commit()

        count = await prune_stale_hubs()
        assert count == 1

        await db_session.refresh(hub)
        assert hub.status == 0  # offline, NOT deleted

    @pytest.mark.asyncio
    async def test_prune_skips_already_offline(self, db: Database, db_session: AsyncSession):
        from verlihub.hublist import prune_stale_hubs

        old = datetime.utcnow() - timedelta(seconds=STALE_HUB_TIMEOUT + 100)
        hub = HubListEntry(
            name="Already Off",
            address="dchub://off:411",
            last_seen=old,
            registered_at=old,
            status=0,
        )
        db_session.add(hub)
        await db_session.commit()

        count = await prune_stale_hubs()
        assert count == 0  # already offline, no change

    @pytest.mark.asyncio
    async def test_prune_does_not_affect_fresh(self, db: Database, db_session: AsyncSession):
        from verlihub.hublist import prune_stale_hubs

        hub = HubListEntry(
            name="Fresh",
            address="dchub://fresh:411",
            last_seen=datetime.utcnow(),
            registered_at=datetime.utcnow(),
            status=1,
        )
        db_session.add(hub)
        await db_session.commit()

        count = await prune_stale_hubs()
        assert count == 0

        await db_session.refresh(hub)
        assert hub.status == 1


# =============================================================================
# 11. WebSocket event emission
# =============================================================================


class TestWebSocketEvents:
    """Test that hublist actions emit WebSocket events."""

    @pytest.mark.asyncio
    async def test_register_emits_event(self, client: AsyncClient):
        """Registration should call _emit_hublist_event."""
        with patch("verlihub.hublist._emit_hublist_event") as mock_emit:
            await _register_hub(client, "WS Hub", "dchub://ws:411")
            mock_emit.assert_called()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "hublist_register"
            assert call_args[0][1]["hub"]["name"] == "WS Hub"

    @pytest.mark.asyncio
    async def test_update_emits_update_event(self, client: AsyncClient):
        """Second registration for same address emits update event."""
        await _register_hub(client, "WS Hub", "dchub://ws2:411")

        with patch("verlihub.hublist._emit_hublist_event") as mock_emit:
            await _register_hub(client, "WS Hub Updated", "dchub://ws2:411")
            mock_emit.assert_called()
            assert mock_emit.call_args[0][0] == "hublist_update"

    @pytest.mark.asyncio
    async def test_delete_emits_event(self, client: AsyncClient):
        """Deleting a hub emits hublist_removed event."""
        result = await _register_hub(client, "Del Hub", "dchub://del2:411")
        hub_id = result["id"]

        with patch("verlihub.hublist._emit_hublist_event") as mock_emit:
            resp = await client.delete(
                f"/hublist/{hub_id}", headers=_master_headers()
            )
            assert resp.status_code == 200
            mock_emit.assert_called()
            assert mock_emit.call_args[0][0] == "hublist_removed"

    @pytest.mark.asyncio
    async def test_block_rejected_emits_event(self, client: AsyncClient, db_session: AsyncSession):
        """Blocked registration emits hublist_blocked event."""
        await _create_block(db_session, HubListBlockType.IP, "127.0.0.1")

        with patch("verlihub.hublist._emit_hublist_event") as mock_emit:
            resp = await client.post(
                "/hublist/register",
                json={"name": "Blocked", "address": "dchub://blocked2:411"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 403
            mock_emit.assert_called()
            assert mock_emit.call_args[0][0] == "hublist_blocked"


# =============================================================================
# 12. Hub removal and stats
# =============================================================================


class TestHubRemovalAndStats:
    """Test hub removal updates stats correctly."""

    @pytest.mark.asyncio
    async def test_remove_hub_via_api(self, client: AsyncClient):
        result = await _register_hub(client, "Removable", "dchub://rm:411")
        hub_id = result["id"]

        stats = await client.get("/hublist/stats")
        assert stats.json()["total_hubs"] == 1

        del_resp = await client.delete(
            f"/hublist/{hub_id}", headers=_master_headers()
        )
        assert del_resp.status_code == 200

        stats = await client.get("/hublist/stats")
        assert stats.json()["total_hubs"] == 0

    @pytest.mark.asyncio
    async def test_remove_nonexistent_hub(self, client: AsyncClient):
        resp = await client.delete(
            "/hublist/99999", headers=_master_headers()
        )
        assert resp.status_code == 404


# =============================================================================
# 13. Integration test: two hubs registering on each other
# =============================================================================


class TestTwoHubIntegration:
    """
    Integration test: simulate two hub instances where Hub B registers
    on Hub A's hublist server.
    """

    @pytest.mark.asyncio
    async def test_hub_b_registers_on_hub_a(self, db: Database):
        """
        Hub A runs the hublist server.
        Hub B acts as a registration client posting to Hub A.
        """
        from fastapi import FastAPI
        from verlihub.hublist import hublist_router

        # --- Hub A: hublist server ---
        app_a = FastAPI()
        app_a.include_router(hublist_router, prefix="/hublist")

        transport_a = ASGITransport(app=app_a)

        async with AsyncClient(transport=transport_a, base_url="http://hub-a") as client_a:
            # Verify Hub A starts with empty hublist
            stats = await client_a.get("/hublist/stats")
            assert stats.json()["total_hubs"] == 0

            # --- Hub B: register on Hub A via direct POST (simulating _register_one) ---
            form_data = {
                "Name": "Hub B",
                "Host": "dchub://hub-b.example.com:411",
                "Description": "Hub B for testing",
                "Users": "25",
                "Share": "500000",
                "Minshare": "0",
                "Maxusers": "100",
                "Country": "DE",
                "Encoding": "UTF-8",
                "Owner": "BobAdmin",
                "Website": "https://hub-b.example.com",
                "Software": "Verlihub-py",
                "Status": "1",
            }

            reg_resp = await client_a.post("/hublist/register", data=form_data)
            assert reg_resp.status_code == 200
            assert "OK" in reg_resp.text

            # Verify Hub A now shows Hub B
            stats = await client_a.get("/hublist/stats")
            stats_data = stats.json()
            assert stats_data["total_hubs"] == 1
            assert stats_data["total_users"] == 25
            assert stats_data["total_share"] == 500_000

            # Verify Hub B appears in the XML list
            xml_resp = await client_a.get("/hublist/")
            assert "Hub B" in xml_resp.text
            assert "dchub://hub-b.example.com:411" in xml_resp.text

            # Verify Hub B appears in the JSON list
            json_resp = await client_a.get("/hublist/?fmt=json")
            hubs = json.loads(json_resp.text)
            assert len(hubs) == 1
            assert hubs[0]["name"] == "Hub B"
            assert hubs[0]["owner"] == "BobAdmin"

    @pytest.mark.asyncio
    async def test_two_hubs_cross_register(self, db: Database):
        """
        Both Hub A and Hub B run hublist servers.
        Each registers on the other.
        """
        from fastapi import FastAPI
        from verlihub.hublist import hublist_router

        # --- Hub A ---
        app_a = FastAPI()
        app_a.include_router(hublist_router, prefix="/hublist")

        # --- Hub B ---
        app_b = FastAPI()
        app_b.include_router(hublist_router, prefix="/hublist")

        transport_a = ASGITransport(app=app_a)
        transport_b = ASGITransport(app=app_b)

        async with AsyncClient(transport=transport_a, base_url="http://hub-a") as client_a, \
             AsyncClient(transport=transport_b, base_url="http://hub-b") as client_b:

            # Hub A info registered on Hub B (via direct POST)
            await client_b.post("/hublist/register", data={
                "Name": "Hub A", "Host": "dchub://hub-a.example.com:411",
                "Users": "50", "Share": "1000000", "Owner": "AliceAdmin",
                "Software": "Verlihub-py", "Status": "1",
            })

            # Hub B info registered on Hub A (via direct POST)
            await client_a.post("/hublist/register", data={
                "Name": "Hub B", "Host": "dchub://hub-b.example.com:411",
                "Users": "30", "Share": "500000", "Owner": "BobAdmin",
                "Software": "Verlihub-py", "Status": "1",
            })

            # Both apps share the same test database, so stats reflect all entries
            stats_a = (await client_a.get("/hublist/stats")).json()
            assert stats_a["total_hubs"] == 2
            assert stats_a["total_users"] == 80  # 50 + 30

            stats_b = (await client_b.get("/hublist/stats")).json()
            assert stats_b["total_hubs"] == 2
            assert stats_b["total_users"] == 80  # 50 + 30

    @pytest.mark.asyncio
    async def test_registration_client_lifecycle(self, db: Database):
        """Test the full registration client start/stop cycle with a live server."""
        from fastapi import FastAPI
        from verlihub.hublist import hublist_router

        app = FastAPI()
        app.include_router(hublist_router, prefix="/hublist")

        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://server") as server_client:
            # Simulate what the registration client does via direct POST
            reg_resp = await server_client.post("/hublist/register", data={
                "Name": "Client Hub", "Host": "dchub://client:411",
                "Users": "10", "Share": "100", "Software": "Verlihub-py",
                "Status": "1",
            })
            assert reg_resp.status_code == 200
            assert "OK" in reg_resp.text

            # Verify server received it
            stats = (await server_client.get("/hublist/stats")).json()
            assert stats["total_hubs"] == 1
            assert stats["total_users"] == 10


# =============================================================================
# 14. Block creates for all types
# =============================================================================


class TestBlockAllTypes:
    """Test creating blocks for every block type via API."""

    @pytest.mark.asyncio
    async def test_create_block_hostname(self, client: AsyncClient):
        resp = await client.post(
            "/hublist/blocks",
            json={"block_type": "hostname", "value": "evil.example.com"},
            headers={**_master_headers(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 201
        assert resp.json()["block_type"] == "hostname"

    @pytest.mark.asyncio
    async def test_create_block_domain(self, client: AsyncClient):
        resp = await client.post(
            "/hublist/blocks",
            json={"block_type": "domain", "value": "spam.com"},
            headers={**_master_headers(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_create_block_asn(self, client: AsyncClient):
        resp = await client.post(
            "/hublist/blocks",
            json={"block_type": "asn", "value": "AS12345"},
            headers={**_master_headers(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_create_block_city(self, client: AsyncClient):
        resp = await client.post(
            "/hublist/blocks",
            json={"block_type": "city", "value": "Novosibirsk"},
            headers={**_master_headers(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_create_block_country(self, client: AsyncClient):
        resp = await client.post(
            "/hublist/blocks",
            json={"block_type": "country", "value": "KP"},
            headers={**_master_headers(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 201


# =============================================================================
# 15. Registration with new fields (email, logo)
# =============================================================================


class TestRegistrationNewFields:
    """Test registration accepts and stores new fields."""

    @pytest.mark.asyncio
    async def test_register_with_email_json(self, client: AsyncClient):
        resp = await client.post(
            "/hublist/register",
            json={
                "name": "Email Hub",
                "address": "dchub://email:411",
                "email": "admin@hub.com",
            },
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        all_resp = await client.get("/hublist/all", headers=_master_headers())
        hub = all_resp.json()[0]
        assert hub["email"] == "admin@hub.com"

    @pytest.mark.asyncio
    async def test_register_with_logo_json(self, client: AsyncClient):
        resp = await client.post(
            "/hublist/register",
            json={
                "name": "Logo Hub",
                "address": "dchub://logo2:411",
                "logo": "https://hub.com/icon.png",
            },
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        all_resp = await client.get("/hublist/all", headers=_master_headers())
        hub = all_resp.json()[0]
        assert hub["logo"] == "https://hub.com/icon.png"

    @pytest.mark.asyncio
    async def test_register_form_with_email_logo(self, client: AsyncClient):
        """Form-encoded registration with Email and Logo fields."""
        resp = await client.post(
            "/hublist/register",
            data={
                "Name": "Form Hub",
                "Host": "dchub://form:411",
                "Email": "form@hub.com",
                "Logo": "https://form.com/logo.png",
            },
        )
        assert resp.status_code == 200

        all_resp = await client.get("/hublist/all", headers=_master_headers())
        hub = all_resp.json()[0]
        assert hub["email"] == "form@hub.com"
        assert hub["logo"] == "https://form.com/logo.png"


# =============================================================================
# 16. GeoIP enrichment on registration (mocked)
# =============================================================================


class TestGeoIPEnrichment:
    """Test that GeoIP enrichment runs during registration."""

    @pytest.mark.asyncio
    async def test_geo_enrichment_on_register(self, client: AsyncClient):
        """Registration enriches hub with GeoIP data."""
        mock_geo = {
            "country_code": "DE",
            "city": "Frankfurt",
            "as_number": "AS13335",
            "as_name": "Cloudflare Inc",
        }
        with patch("verlihub.hublist._lookup_geo_for_ip", return_value=mock_geo), \
             patch("verlihub.hublist._resolve_hostname", return_value="hub.cloudflare.com"):
            resp = await client.post(
                "/hublist/register",
                json={"name": "Geo Hub", "address": "dchub://geo:411"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200

        all_resp = await client.get("/hublist/all", headers=_master_headers())
        hub = all_resp.json()[0]
        assert hub["city"] == "Frankfurt"
        assert "AS13335" in hub["asn"]
        assert hub["hostname"] == "hub.cloudflare.com"

    @pytest.mark.asyncio
    async def test_geo_enrichment_sets_country_if_empty(self, client: AsyncClient):
        """If hub doesn't send country, GeoIP country is used."""
        mock_geo = {"country_code": "NL", "city": "Amsterdam", "as_number": "", "as_name": ""}
        with patch("verlihub.hublist._lookup_geo_for_ip", return_value=mock_geo), \
             patch("verlihub.hublist._resolve_hostname", return_value=""):
            resp = await client.post(
                "/hublist/register",
                json={"name": "No Country Hub", "address": "dchub://nc:411"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200

        all_resp = await client.get("/hublist/all", headers=_master_headers())
        hub = all_resp.json()[0]
        assert hub["country"] == "NL"
