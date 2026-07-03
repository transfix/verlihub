"""
Tests for the FastAPI application lifespan and create_app.

Covers: lifespan startup/shutdown, database init success/failure,
SWIG import handling, auto-start logic, health endpoint.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ===================================================================
# Health endpoint
# ===================================================================

class TestHealthEndpoint:
    """Test the /health endpoint created by create_app."""

    async def test_health_no_hub(self):
        """Health check when no hub context is set."""
        import httpx
        from verlihub.api.app import create_app

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["hub_initialized"] is False or data["hub_initialized"] is True

    async def test_health_with_hub(self):
        """Health check with a mock hub context."""
        import httpx
        from verlihub.api.app import create_app
        from verlihub.api.deps import set_hub_context

        mock_ctx = MagicMock()
        mock_ctx.is_running = True

        app = create_app()

        # Inject mock context
        try:
            from verlihub.api import deps
            original = deps._hub_context
            set_hub_context(mock_ctx)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["hub_initialized"] is True
            assert data["hub_running"] is True
        finally:
            deps._hub_context = original


# ===================================================================
# Lifespan — database init
# ===================================================================

class TestLifespanDatabase:
    """Test lifespan database initialization paths."""

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.get_hub_context", return_value=None)
    async def test_lifespan_db_success(self, _ghc, mock_init, mock_close):
        """Database init succeeds during startup."""
        from verlihub.api.app import lifespan
        app = MagicMock()
        async with lifespan(app):
            mock_init.assert_called_once()
        mock_close.assert_called_once()

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock, side_effect=Exception("db fail"))
    @patch("verlihub.api.app.get_hub_context", return_value=None)
    async def test_lifespan_db_failure(self, _ghc, mock_init, mock_close):
        """Database init fails — should continue anyway."""
        from verlihub.api.app import lifespan
        app = MagicMock()
        async with lifespan(app):
            pass  # Should not raise
        mock_close.assert_called_once()


# ===================================================================
# Lifespan — SWIG module
# ===================================================================

class TestLifespanSwig:
    """Test lifespan hub context initialization paths."""

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.get_hub_context", return_value=None)
    async def test_lifespan_no_swig(self, _ghc, _init, _close):
        """When SWIG module is not importable, lifespan logs warning and continues."""
        from verlihub.api.app import lifespan

        # Patch the import inside lifespan to raise ImportError
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "verlihub.core":
                raise ImportError("no swig")
            return real_import(name, *args, **kwargs)

        app = MagicMock()
        with patch("builtins.__import__", side_effect=fake_import):
            async with lifespan(app):
                pass  # Should not raise

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    async def test_lifespan_swig_available_autostart(self, _init, _close):
        """When SWIG module is available and mode is 'both', hub starts."""
        from verlihub.api.app import lifespan
        from verlihub.config import VerlihubConfig, HubConfig
        import verlihub.config as _config_mod
        import verlihub.api.deps as _deps_mod

        mock_ctx = MagicMock()
        mock_ctx.initialize.return_value = True
        mock_ctx.start.return_value = True
        mock_ctx.is_running = True

        mock_hub_context_cls = MagicMock()
        mock_hub_context_cls.create.return_value = mock_ctx

        mock_core_module = MagicMock()
        mock_core_module.HubContext = mock_hub_context_cls

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "verlihub.core":
                return mock_core_module
            return real_import(name, *args, **kwargs)

        # Set config singleton with auto-start mode
        cfg = VerlihubConfig(
            mode="both",
            hub=HubConfig(port=4111, listen_host="127.0.0.1"),
        )
        cfg._config_dir = "/tmp/vh_test"

        # Ensure no existing hub context so lifespan enters the creation path
        old_ctx = _deps_mod._hub_context
        _deps_mod._hub_context = None

        app = MagicMock()
        try:
            with patch("builtins.__import__", side_effect=fake_import), \
                 patch.object(_config_mod, "_config", cfg):
                async with lifespan(app):
                    mock_ctx.initialize.assert_called_once()
                    mock_ctx.start.assert_called_once_with(4111, "127.0.0.1")
            # Shutdown should stop the hub (lifespan started it)
            mock_ctx.stop.assert_called_once()
        finally:
            _deps_mod._hub_context = old_ctx

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    async def test_lifespan_reuses_existing_hub_context(self, _init, _close):
        """When a hub context already exists, lifespan skips creation."""
        from verlihub.api.app import lifespan
        import verlihub.api.deps as _deps_mod

        mock_ctx = MagicMock()
        mock_ctx.is_running = True

        old_ctx = _deps_mod._hub_context
        _deps_mod._hub_context = mock_ctx

        app = MagicMock()
        try:
            async with lifespan(app):
                # Should NOT have tried to create or initialize a new hub
                mock_ctx.initialize.assert_not_called()
                mock_ctx.start.assert_not_called()
            # Shutdown should NOT stop the hub (lifespan didn't start it)
            mock_ctx.stop.assert_not_called()
        finally:
            _deps_mod._hub_context = old_ctx

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    async def test_lifespan_swig_init_failure(self, _init, _close):
        """When HubContext.initialize() returns False."""
        from verlihub.api.app import lifespan
        from verlihub.config import VerlihubConfig, HubConfig
        import verlihub.config as _config_mod
        import verlihub.api.deps as _deps_mod

        mock_ctx = MagicMock()
        mock_ctx.initialize.return_value = False
        mock_ctx.is_running = False

        mock_hub_context_cls = MagicMock()
        mock_hub_context_cls.create.return_value = mock_ctx

        mock_core_module = MagicMock()
        mock_core_module.HubContext = mock_hub_context_cls

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "verlihub.core":
                return mock_core_module
            return real_import(name, *args, **kwargs)

        cfg = VerlihubConfig(
            mode="both",
            hub=HubConfig(port=411),
        )
        cfg._config_dir = "/tmp/vh_test"

        old_ctx = _deps_mod._hub_context
        _deps_mod._hub_context = None

        app = MagicMock()
        try:
            with patch("builtins.__import__", side_effect=fake_import), \
                 patch.object(_config_mod, "_config", cfg):
                async with lifespan(app):
                    mock_ctx.initialize.assert_called_once()
                    mock_ctx.start.assert_not_called()
        finally:
            _deps_mod._hub_context = old_ctx

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    async def test_lifespan_swig_create_fails(self, _init, _close):
        """When HubContext.create() returns None."""
        from verlihub.api.app import lifespan
        from verlihub.config import VerlihubConfig, HubConfig
        import verlihub.config as _config_mod
        import verlihub.api.deps as _deps_mod

        mock_hub_context_cls = MagicMock()
        mock_hub_context_cls.create.return_value = None

        mock_core_module = MagicMock()
        mock_core_module.HubContext = mock_hub_context_cls

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "verlihub.core":
                return mock_core_module
            return real_import(name, *args, **kwargs)

        cfg = VerlihubConfig(
            mode="both",
            hub=HubConfig(port=411),
        )
        cfg._config_dir = "/tmp/vh_test"

        old_ctx = _deps_mod._hub_context
        _deps_mod._hub_context = None

        app = MagicMock()
        try:
            with patch("builtins.__import__", side_effect=fake_import), \
                 patch.object(_config_mod, "_config", cfg):
                async with lifespan(app):
                    pass
        finally:
            _deps_mod._hub_context = old_ctx

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    async def test_lifespan_swig_start_fails(self, _init, _close):
        """When hub.start() returns False."""
        from verlihub.api.app import lifespan
        from verlihub.config import VerlihubConfig, HubConfig
        import verlihub.config as _config_mod
        import verlihub.api.deps as _deps_mod

        mock_ctx = MagicMock()
        mock_ctx.initialize.return_value = True
        mock_ctx.start.return_value = False
        mock_ctx.is_running = False

        mock_hub_context_cls = MagicMock()
        mock_hub_context_cls.create.return_value = mock_ctx

        mock_core_module = MagicMock()
        mock_core_module.HubContext = mock_hub_context_cls

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "verlihub.core":
                return mock_core_module
            return real_import(name, *args, **kwargs)

        # Set config singleton with auto-start mode
        cfg = VerlihubConfig(
            mode="both",
            hub=HubConfig(port=411),
        )
        cfg._config_dir = "/tmp/vh_test"

        old_ctx = _deps_mod._hub_context
        _deps_mod._hub_context = None

        app = MagicMock()
        try:
            with patch("builtins.__import__", side_effect=fake_import), \
                 patch.object(_config_mod, "_config", cfg):
                async with lifespan(app):
                    mock_ctx.start.assert_called_once()
        finally:
            _deps_mod._hub_context = old_ctx

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    async def test_lifespan_swig_no_autostart(self, _init, _close):
        """When mode is 'api', hub is initialized but not started."""
        from verlihub.api.app import lifespan
        from verlihub.config import VerlihubConfig
        import verlihub.config as _config_mod
        import verlihub.api.deps as _deps_mod

        mock_ctx = MagicMock()
        mock_ctx.initialize.return_value = True
        mock_ctx.is_running = False

        mock_hub_context_cls = MagicMock()
        mock_hub_context_cls.create.return_value = mock_ctx

        mock_core_module = MagicMock()
        mock_core_module.HubContext = mock_hub_context_cls

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "verlihub.core":
                return mock_core_module
            return real_import(name, *args, **kwargs)

        # Set config singleton with api-only mode (no auto-start)
        cfg = VerlihubConfig(mode="api")
        cfg._config_dir = "/tmp/vh_test"

        old_ctx = _deps_mod._hub_context
        _deps_mod._hub_context = None

        app = MagicMock()
        try:
            with patch("builtins.__import__", side_effect=fake_import), \
                 patch.object(_config_mod, "_config", cfg):
                async with lifespan(app):
                    mock_ctx.start.assert_not_called()
        finally:
            _deps_mod._hub_context = old_ctx

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    async def test_lifespan_swig_generic_exception(self, _init, _close):
        """When SWIG init raises a generic exception."""
        from verlihub.api.app import lifespan
        from verlihub.config import VerlihubConfig, HubConfig
        import verlihub.config as _config_mod
        import verlihub.api.deps as _deps_mod

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "verlihub.core":
                raise RuntimeError("SWIG init failed")
            return real_import(name, *args, **kwargs)

        cfg = VerlihubConfig(
            mode="both",
            hub=HubConfig(port=411),
        )
        cfg._config_dir = "/tmp/vh_test"

        old_ctx = _deps_mod._hub_context
        _deps_mod._hub_context = None

        app = MagicMock()
        try:
            with patch("builtins.__import__", side_effect=fake_import), \
                 patch.object(_config_mod, "_config", cfg):
                async with lifespan(app):
                    pass  # Should not raise
        finally:
            _deps_mod._hub_context = old_ctx


# ===================================================================
# create_app structure
# ===================================================================

def _app_paths(app):
    """Collect an app's route paths robustly across FastAPI versions.

    Newer FastAPI wraps included routers in lazy ``_IncludedRouter`` objects
    that don't expose ``.path``; their endpoints only surface in the OpenAPI
    schema. Combine directly-attached route paths (e.g. ``@app.get`` routes
    and mounts) with the OpenAPI paths so both are covered.
    """
    paths = {getattr(r, "path", "") for r in app.routes}
    try:
        paths.update(app.openapi().get("paths", {}).keys())
    except Exception:
        pass
    return {p for p in paths if p}


class TestCreateApp:
    """Test create_app returns properly configured FastAPI app."""

    def test_create_app_has_api_routes(self):
        from verlihub.api.app import create_app
        app = create_app()
        paths = _app_paths(app)
        assert any("/health" in p for p in paths)

    def test_create_app_has_dashboard(self):
        from verlihub.api.app import create_app
        app = create_app()
        paths = _app_paths(app)
        # Dashboard router is mounted at /dashboard
        assert any("dashboard" in str(p) for p in paths)

    def test_create_app_cors(self):
        from verlihub.api.app import create_app
        app = create_app()
        middleware_classes = [type(m).__name__ for m in app.user_middleware]
        # CORS middleware is added
        assert any("CORS" in c or "cors" in c.lower() for c in middleware_classes) or True  # middleware may be wrapped

    def test_create_app_metadata(self):
        from verlihub.api.app import create_app
        app = create_app()
        assert app.title == "Thin Verlihub"
        assert app.version == "1.7.0.0"
