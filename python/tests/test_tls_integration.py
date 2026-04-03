"""Tests for TLS configuration pipeline.

Verifies that TLS settings flow correctly from YAML config through
the Docker compose generation and database configuration for both
the 'py' and 'legacy' editions.

Tests cover:
- apply_config.py SQL generation for TLS settings
- Compose file generation (TLS proxy sidecar, certbot sidecar)
- $MyIP protocol handling in the NMDC hub server
- UserInfoSnapshot tls_version field via SWIG
- user_info.py Hub TLS display
"""

from __future__ import annotations

import importlib
import os
import subprocess
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

# Locate the repository root (two levels up from this test file)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ============================================================================
# Helpers
# ============================================================================

def _apply_config_sql(yaml_text: str) -> str:
    """Run apply_config.py --dry-run on *yaml_text* and return raw output."""
    config_path = REPO_ROOT / "docker" / "apply_config.py"
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yml", delete=False
    ) as tmp:
        tmp.write(yaml_text)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ["python3", str(config_path), "--config", tmp_path, "--dry-run"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout
    finally:
        os.unlink(tmp_path)


def _parse_sql_values(output: str) -> dict[str, str]:
    """Parse the generated SQL INSERT output into a {db_var: value} dict."""
    values: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip().rstrip(",")
        if line.startswith("('config',"):
            # ('config', 'var_name', 'value')
            parts = line.strip("()").split("', '")
            if len(parts) == 3:
                var = parts[1]
                val = parts[2].rstrip("')")
                values[var] = val
    return values


def _compose_snippet(yaml_text: str, edition: str = "py") -> str:
    """Source run_production.sh parse_config + compose functions, return output.

    This shells out to bash rather than trying to parse the heredocs in Python.
    """
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yml", delete=False
    ) as tmp:
        tmp.write(yaml_text)
        tmp_path = tmp.name

    script = REPO_ROOT / "run_production.sh"
    bash_code = textwrap.dedent(f"""\
        set -e

        # Extract functions into a temp file and source it
        # (avoids /dev/fd issues with heredoc redirections in process substitution)
        _tmpscript=$(mktemp)
        trap "rm -f $_tmpscript" EXIT
        sed -n '1,/^main()/p' "{script}" | head -n -1 > "$_tmpscript"
        source "$_tmpscript"
        rm -f "$_tmpscript"

        # Override log helpers AFTER sourcing to suppress ANSI output
        log_info()    {{ :; }}
        log_success() {{ :; }}
        log_warn()    {{ :; }}
        log_error()   {{ :; }}

        # Parse config from the temp YAML
        CONFIG_FILE="{tmp_path}"
        eval "$(parse_config)"

        # Set edition explicitly (parse_config sets YAML_EDITION, not EDITION)
        EDITION="{edition}"
        if [ -z "$EDITION" ]; then
            if [ -n "$YAML_EDITION" ]; then
                EDITION="$YAML_EDITION"
            else
                EDITION="legacy"
            fi
        fi

        generate_compose 2>/dev/null
        cat docker-compose.production.yml
        rm -f docker-compose.production.yml
    """)

    try:
        result = subprocess.run(
            ["bash", "-c", bash_code],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(REPO_ROOT),
        )
        return result.stdout
    finally:
        os.unlink(tmp_path)


# Minimal YAML config for a TLS-enabled py hub
_TLS_PY_YAML = textwrap.dedent("""\
    edition: py
    database:
      type: mysql
      host: mysql
      name: verlihub
      user: verlihub
      password: verlihub
    hub:
      name: "TLS Test Hub"
      port: 4111
    users:
      masters:
        - nick: admin
          password: admin
    api:
      enabled: true
      port: 30000
    tls:
      enabled: true
      port: 411
      only_mode: false
      min_version: 2
      cert_org: "Test Org"
      cert_email: "test@example.com"
      cert_host: "hub.example.com"
""")

_TLS_LE_YAML = textwrap.dedent("""\
    edition: py
    database:
      type: mysql
      host: mysql
      name: verlihub
      user: verlihub
      password: verlihub
    hub:
      name: "LE Test Hub"
      port: 4111
    users:
      masters:
        - nick: admin
          password: admin
    api:
      enabled: true
      port: 30000
    tls:
      enabled: true
      port: 411
      cert_host: "hub.example.com"
      letsencrypt:
        enabled: true
        domain: "hub.example.com"
        email: "admin@example.com"
        staging: true
""")

_TLS_LEGACY_YAML = textwrap.dedent("""\
    database:
      type: mysql
      host: mysql
      name: verlihub
      user: verlihub
      password: verlihub
    hub:
      name: "Legacy TLS Hub"
      port: 4111
    users:
      masters:
        - nick: admin
          password: admin
    tls:
      enabled: true
      port: 411
      only_mode: true
      min_version: 3
      cert_org: "Legacy Org"
      cert_email: "legacy@example.com"
      cert_host: "legacy.example.com"
""")

_NO_TLS_YAML = textwrap.dedent("""\
    database:
      type: mysql
      host: mysql
      name: verlihub
      user: verlihub
      password: verlihub
    hub:
      name: "No TLS Hub"
      port: 4111
    users:
      masters:
        - nick: admin
          password: admin
""")


# ============================================================================
# apply_config.py — SQL generation for TLS settings
# ============================================================================

class TestApplyConfigTLS:
    """Verify TLS settings are correctly translated to SQL for legacy edition."""

    def test_tls_enabled_generates_sql(self):
        output = _apply_config_sql(_TLS_LEGACY_YAML)
        vals = _parse_sql_values(output)
        assert "tls_listen_port" in vals
        assert vals["tls_listen_port"] == "411"

    def test_tls_only_mode(self):
        output = _apply_config_sql(_TLS_LEGACY_YAML)
        vals = _parse_sql_values(output)
        assert vals.get("tls_only_mode") == "1"

    def test_tls_min_version(self):
        output = _apply_config_sql(_TLS_LEGACY_YAML)
        vals = _parse_sql_values(output)
        assert vals.get("tls_min_ver") == "3"

    def test_tls_cert_org(self):
        output = _apply_config_sql(_TLS_LEGACY_YAML)
        vals = _parse_sql_values(output)
        assert vals.get("tls_cert_org") == "Legacy Org"

    def test_tls_cert_email(self):
        output = _apply_config_sql(_TLS_LEGACY_YAML)
        vals = _parse_sql_values(output)
        assert vals.get("tls_cert_mail") == "legacy@example.com"

    def test_tls_cert_host(self):
        output = _apply_config_sql(_TLS_LEGACY_YAML)
        vals = _parse_sql_values(output)
        assert vals.get("tls_cert_host") == "legacy.example.com"

    def test_tls_disabled_sets_port_zero(self):
        output = _apply_config_sql(_NO_TLS_YAML)
        vals = _parse_sql_values(output)
        # When TLS is disabled, tls_listen_port should be set to 0
        assert vals.get("tls_listen_port") == "0"

    def test_tls_disabled_skips_other_tls_fields(self):
        output = _apply_config_sql(_NO_TLS_YAML)
        vals = _parse_sql_values(output)
        assert "tls_only_mode" not in vals
        assert "tls_min_ver" not in vals
        assert "tls_cert_org" not in vals
        assert "tls_cert_mail" not in vals
        assert "tls_cert_host" not in vals


# ============================================================================
# Compose generation — TLS proxy sidecar
# ============================================================================

@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent.parent / "run_production.sh").exists(),
    reason="run_production.sh not found (running inside Docker image without full repo)",
)
class TestComposeGenerationTLS:
    """Verify docker-compose.production.yml includes TLS services."""

    def test_tls_proxy_service_present(self):
        compose = _compose_snippet(_TLS_PY_YAML, edition="py")
        assert "vh-prod-tls:" in compose or "-tls:" in compose

    def test_tls_proxy_image_builds_from_dockerfile(self):
        compose = _compose_snippet(_TLS_PY_YAML, edition="py")
        assert "Dockerfile.tls-proxy" in compose

    def test_tls_proxy_port_mapping(self):
        compose = _compose_snippet(_TLS_PY_YAML, edition="py")
        assert '"411:411"' in compose

    def test_tls_proxy_forwards_to_hub(self):
        compose = _compose_snippet(_TLS_PY_YAML, edition="py")
        # Should contain -hub <prefix>-hub:4111
        assert "hub:4111" in compose

    def test_tls_proxy_cert_org(self):
        compose = _compose_snippet(_TLS_PY_YAML, edition="py")
        assert "Test Org" in compose

    def test_tls_proxy_cert_host(self):
        compose = _compose_snippet(_TLS_PY_YAML, edition="py")
        assert "hub.example.com" in compose

    def test_tls_cert_volume(self):
        compose = _compose_snippet(_TLS_PY_YAML, edition="py")
        assert "certs:" in compose

    def test_no_tls_no_proxy(self):
        compose = _compose_snippet(_NO_TLS_YAML, edition="py")
        assert "tls-proxy" not in compose.lower() or "Dockerfile.tls-proxy" not in compose

    def test_certbot_service_when_le_enabled(self):
        compose = _compose_snippet(_TLS_LE_YAML, edition="py")
        assert "certbot" in compose

    def test_certbot_domain(self):
        compose = _compose_snippet(_TLS_LE_YAML, edition="py")
        assert "hub.example.com" in compose

    def test_certbot_staging_flag(self):
        compose = _compose_snippet(_TLS_LE_YAML, edition="py")
        # LE_STAGING should be "1" for staging: true
        assert 'LE_STAGING: "1"' in compose

    def test_certbot_port_80(self):
        compose = _compose_snippet(_TLS_LE_YAML, edition="py")
        assert '"80:80"' in compose

    def test_letsencrypt_volume(self):
        compose = _compose_snippet(_TLS_LE_YAML, edition="py")
        assert "letsencrypt:" in compose

    def test_no_certbot_without_le(self):
        compose = _compose_snippet(_TLS_PY_YAML, edition="py")
        assert "certbot" not in compose

    def test_legacy_edition_gets_tls_proxy(self):
        compose = _compose_snippet(_TLS_LEGACY_YAML, edition="legacy")
        assert "Dockerfile.tls-proxy" in compose

    def test_legacy_tls_only_mode_wait_zero(self):
        compose = _compose_snippet(_TLS_LEGACY_YAML, edition="legacy")
        # only_mode: true → -wait 0ms
        assert "-wait 0ms" in compose or "wait 0" in compose


# ============================================================================
# $MyIP protocol handling — UserInfoSnapshot.tls_version
# ============================================================================

class TestMyIPProtocol:
    """Verify $MyIP handling and tls_version exposure via SWIG."""

    @pytest.fixture(autouse=True)
    def _skip_without_swig(self):
        """Skip tests if the SWIG module or tls_version field isn't available."""
        try:
            from verlihub import verlihub_core
        except ImportError:
            pytest.skip("verlihub SWIG module not available")
        snap = verlihub_core.UserInfoSnapshot()
        if not hasattr(snap, "tls_version"):
            pytest.skip("UserInfoSnapshot.tls_version not in this SWIG build")

    def test_tls_version_field_exists(self):
        from verlihub import verlihub_core
        snap = verlihub_core.UserInfoSnapshot()
        assert hasattr(snap, "tls_version")

    def test_tls_version_default_empty(self):
        from verlihub import verlihub_core
        snap = verlihub_core.UserInfoSnapshot()
        assert snap.tls_version == ""

    def test_tls_version_settable(self):
        from verlihub import verlihub_core
        snap = verlihub_core.UserInfoSnapshot()
        snap.tls_version = "1.3"
        assert snap.tls_version == "1.3"

    def test_tls_version_roundtrips_12(self):
        from verlihub import verlihub_core
        snap = verlihub_core.UserInfoSnapshot()
        snap.tls_version = "1.2"
        assert snap.tls_version == "1.2"


# ============================================================================
# user_info.py — Hub TLS display
# ============================================================================

class TestUserInfoTLSDisplay:
    """Verify user_info renders Hub TLS from the info dict."""

    def test_hub_tls_no_when_absent(self):
        from verlihub.user_info import _format_info
        text = _format_info({
            "nick": "test",
            "ip": "10.0.0.1",
            "status_flag": 0,
        })
        assert "Hub TLS: No" in text

    def test_hub_tls_no_when_empty_string(self):
        from verlihub.user_info import _format_info
        text = _format_info({
            "nick": "test",
            "ip": "10.0.0.1",
            "tls_version": "",
            "status_flag": 0,
        })
        assert "Hub TLS: No" in text

    def test_hub_tls_version_13(self):
        from verlihub.user_info import _format_info
        text = _format_info({
            "nick": "test",
            "ip": "10.0.0.1",
            "tls_version": "1.3",
            "status_flag": 0,
        })
        assert "Hub TLS: 1.3" in text

    def test_hub_tls_version_12(self):
        from verlihub.user_info import _format_info
        text = _format_info({
            "nick": "test",
            "ip": "10.0.0.1",
            "tls_version": "1.2",
            "status_flag": 0,
        })
        assert "Hub TLS: 1.2" in text


# ============================================================================
# Docker compose config file — TLS test config
# ============================================================================

@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent.parent / "production.example.yml").exists(),
    reason="production.example.yml not found (running inside Docker image without full repo)",
)
class TestTLSConfigFiles:
    """Verify the TLS-related YAML config files are well-formed."""

    def test_production_example_has_tls_section(self):
        with open(REPO_ROOT / "production.example.yml") as f:
            config = yaml.safe_load(f)
        assert "tls" in config
        tls = config["tls"]
        assert "enabled" in tls
        assert "port" in tls
        assert "letsencrypt" in tls

    def test_production_example_le_defaults(self):
        with open(REPO_ROOT / "production.example.yml") as f:
            config = yaml.safe_load(f)
        le = config["tls"]["letsencrypt"]
        assert le["enabled"] is False
        assert "domain" in le
        assert "email" in le

    def test_production_yml_has_tls_section(self):
        prod = REPO_ROOT / "production.yml"
        if not prod.exists():
            pytest.skip("production.yml not present")
        with open(prod) as f:
            config = yaml.safe_load(f)
        assert "tls" in config

    def test_dockerfile_tls_proxy_exists(self):
        assert (REPO_ROOT / "docker" / "Dockerfile.tls-proxy").is_file()

    def test_certbot_entrypoint_exists_and_executable(self):
        path = REPO_ROOT / "docker" / "certbot-entrypoint.sh"
        assert path.is_file()
        assert os.access(path, os.X_OK)

    def test_tls_test_config_valid_yaml(self):
        """Verify our test YAML configs parse without error."""
        for yaml_text in (_TLS_PY_YAML, _TLS_LE_YAML, _TLS_LEGACY_YAML, _NO_TLS_YAML):
            config = yaml.safe_load(yaml_text)
            assert isinstance(config, dict)
