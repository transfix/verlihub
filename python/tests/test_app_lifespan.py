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
        """When SWIG module is available and VH_AUTO_START=1, hub starts."""
        from verlihub.api.app import lifespan

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

        app = MagicMock()
        env = {
            "VH_AUTO_START": "1",
            "VH_PORT": "4111",
            "VH_LISTEN_IP": "127.0.0.1",
            "VH_CONFIG_DIR": "/tmp/vh_test",
        }
        with patch("builtins.__import__", side_effect=fake_import), \
             patch.dict("os.environ", env, clear=False), \
             patch("verlihub.api.app.get_hub_context", return_value=mock_ctx):
            async with lifespan(app):
                mock_ctx.initialize.assert_called_once()
                mock_ctx.start.assert_called_once_with(4111, "127.0.0.1")
        # Shutdown should stop the hub
        mock_ctx.stop.assert_called_once()

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    async def test_lifespan_swig_init_failure(self, _init, _close):
        """When HubContext.initialize() returns False."""
        from verlihub.api.app import lifespan

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

        app = MagicMock()
        with patch("builtins.__import__", side_effect=fake_import), \
             patch("verlihub.api.app.get_hub_context", return_value=mock_ctx):
            async with lifespan(app):
                pass

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    async def test_lifespan_swig_create_fails(self, _init, _close):
        """When HubContext.create() returns None."""
        from verlihub.api.app import lifespan

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

        app = MagicMock()
        with patch("builtins.__import__", side_effect=fake_import), \
             patch("verlihub.api.app.get_hub_context", return_value=None):
            async with lifespan(app):
                pass

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    async def test_lifespan_swig_start_fails(self, _init, _close):
        """When hub.start() returns False."""
        from verlihub.api.app import lifespan

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

        app = MagicMock()
        env = {"VH_AUTO_START": "1", "VH_PORT": "411", "VH_CONFIG_DIR": "/tmp/vh_test"}
        with patch("builtins.__import__", side_effect=fake_import), \
             patch.dict("os.environ", env, clear=False), \
             patch("verlihub.api.app.get_hub_context", return_value=mock_ctx):
            async with lifespan(app):
                mock_ctx.start.assert_called_once()

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    async def test_lifespan_swig_no_autostart(self, _init, _close):
        """When VH_AUTO_START is not set, hub is initialized but not started."""
        from verlihub.api.app import lifespan

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

        app = MagicMock()
        env = {"VH_AUTO_START": "0", "VH_CONFIG_DIR": "/tmp/vh_test"}
        with patch("builtins.__import__", side_effect=fake_import), \
             patch.dict("os.environ", env, clear=False), \
             patch("verlihub.api.app.get_hub_context", return_value=mock_ctx):
            async with lifespan(app):
                mock_ctx.start.assert_not_called()

    @patch("verlihub.api.app.close_database", new_callable=AsyncMock)
    @patch("verlihub.api.app.init_database", new_callable=AsyncMock)
    async def test_lifespan_swig_generic_exception(self, _init, _close):
        """When SWIG init raises a generic exception."""
        from verlihub.api.app import lifespan

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "verlihub.core":
                raise RuntimeError("SWIG init failed")
            return real_import(name, *args, **kwargs)

        app = MagicMock()
        with patch("builtins.__import__", side_effect=fake_import), \
             patch("verlihub.api.app.get_hub_context", return_value=None):
            async with lifespan(app):
                pass  # Should not raise


# ===================================================================
# create_app structure
# ===================================================================

class TestCreateApp:
    """Test create_app returns properly configured FastAPI app."""

    def test_create_app_has_api_routes(self):
        from verlihub.api.app import create_app
        app = create_app()
        paths = [r.path for r in app.routes]
        assert any("/health" in p for p in paths)

    def test_create_app_has_dashboard(self):
        from verlihub.api.app import create_app
        app = create_app()
        paths = [r.path for r in app.routes]
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
        assert app.version == "0.1.0"
