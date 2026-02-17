"""
SQLModel models for Verlihub database.

These models mirror the existing MySQL tables used by Verlihub,
allowing the Python layer to read/write hub data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import IntEnum
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


def utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


class UserClass(IntEnum):
    """User class levels (matches C++ enum)."""
    GUEST = -1
    PINGER = 0
    REGISTERED = 1
    VIP = 2
    OPERATOR = 3
    CHEEF = 4  # Chief operator
    ADMIN = 5
    MASTER = 10


# =============================================================================
# Core Models
# =============================================================================


class SetupListBase(SQLModel):
    """Base for SetupList (hub configuration)."""
    file: str = Field(primary_key=True)
    var: str = Field(primary_key=True)
    val: str = ""


class SetupList(SetupListBase, table=True):
    """Hub configuration storage (maps to SetupList table)."""
    __tablename__ = "SetupList"


# =============================================================================
# User Models
# =============================================================================


class RegUserBase(SQLModel):
    """Base model for registered users."""
    nick: str = Field(index=True, max_length=64)
    login_pwd: str = Field(default="", max_length=128)  # Hashed password
    login_last_ip: str = Field(default="", max_length=45)  # IPv4/IPv6
    login_last: Optional[datetime] = None
    login_count: int = 0
    user_class: int = Field(default=UserClass.REGISTERED)
    user_class_ex: int = 0
    reg_date: datetime = Field(default_factory=utc_now)
    reg_op: str = Field(default="", max_length=64)
    authorised: bool = True
    hide_share: bool = False
    hide_keys: bool = False
    hide_ctm: bool = False
    show_kick: bool = True
    note_op: str = Field(default="", max_length=255)
    note_usr: str = Field(default="", max_length=255)
    alt_ip: str = Field(default="", max_length=45)


class RegUser(RegUserBase, table=True):
    """Registered user in the hub."""
    __tablename__ = "reglist"
    
    id: Optional[int] = Field(default=None, primary_key=True)


class RegUserCreate(RegUserBase):
    """Schema for creating a new registered user."""
    pass


class RegUserRead(RegUserBase):
    """Schema for reading a registered user."""
    id: int


class RegUserUpdate(SQLModel):
    """Schema for updating a registered user."""
    login_pwd: Optional[str] = None
    user_class: Optional[int] = None
    authorised: Optional[bool] = None
    note_op: Optional[str] = None


# =============================================================================
# Ban Models
# =============================================================================


class BanType(IntEnum):
    """Ban type flags (can be combined)."""
    NICK = 1
    IP = 2
    RANGE = 4
    HOST1 = 8
    HOST2 = 16
    HOST3 = 32
    SHARE = 64
    PREFIX = 128
    HOSTR1 = 256


class BanBase(SQLModel):
    """Base model for bans."""
    ip: str = Field(default="", max_length=45, index=True)
    nick: str = Field(default="", max_length=64, index=True)
    ban_type: int = Field(default=BanType.IP)
    host: str = Field(default="", max_length=255)
    share_size: int = 0
    date_start: datetime = Field(default_factory=utc_now)
    date_limit: Optional[datetime] = None
    nick_op: str = Field(default="", max_length=64)
    reason: str = Field(default="", max_length=512)
    last_hit: Optional[datetime] = None
    this_kick: bool = False


class Ban(BanBase, table=True):
    """Active ban in the hub."""
    __tablename__ = "banlist"
    
    id: Optional[int] = Field(default=None, primary_key=True)


class BanCreate(BanBase):
    """Schema for creating a new ban."""
    pass


class BanRead(BanBase):
    """Schema for reading a ban."""
    id: int


# =============================================================================
# Trigger Models (custom commands)
# =============================================================================


class TriggerFlags(IntEnum):
    """Trigger execution flags."""
    EXECUTE = 1  # Execute as command
    SEND_PM = 2  # Send as PM
    SEND_MAIN = 4  # Send to main chat


class TriggerBase(SQLModel):
    """Base model for triggers."""
    command: str = Field(max_length=64, index=True)
    send_as: str = Field(default="", max_length=64)  # Bot name
    def_: str = Field(default="", alias="def")  # Response
    min_class: int = Field(default=UserClass.REGISTERED)
    max_class: int = Field(default=UserClass.MASTER)
    flags: int = Field(default=TriggerFlags.SEND_MAIN)
    seconds: int = 0  # Timeout


class Trigger(TriggerBase, table=True):
    """Custom trigger/command."""
    __tablename__ = "trigger"
    
    id: Optional[int] = Field(default=None, primary_key=True)


# =============================================================================
# Kick Models
# =============================================================================


class KickBase(SQLModel):
    """Base model for kicks (kick history)."""
    nick: str = Field(max_length=64, index=True)
    ip: str = Field(default="", max_length=45)
    op: str = Field(max_length=64, index=True)
    reason: str = Field(default="", max_length=512)
    time: datetime = Field(default_factory=utc_now)
    drop: bool = False  # Dropped from hub (vs. just warned)


class Kick(KickBase, table=True):
    """Kick history entry."""
    __tablename__ = "kicklist"
    
    id: Optional[int] = Field(default=None, primary_key=True)


# =============================================================================
# Redirect Models  
# =============================================================================


class RedirectBase(SQLModel):
    """Base model for custom redirects."""
    address: str = Field(max_length=255)
    flag: int = 0
    enable: bool = True


class Redirect(RedirectBase, table=True):
    """Custom redirect address."""
    __tablename__ = "custom_redirects"
    
    id: Optional[int] = Field(default=None, primary_key=True)


# =============================================================================
# DC Client Models
# =============================================================================


class DCClientBase(SQLModel):
    """Base model for DC client identification."""
    name: str = Field(max_length=64, index=True)
    tag_id: str = Field(max_length=32)
    min_version: float = 0.0
    max_version: float = 0.0
    ban: bool = False
    enable: bool = True


class DCClient(DCClientBase, table=True):
    """DC client identification rules."""
    __tablename__ = "dc_clients"
    
    id: Optional[int] = Field(default=None, primary_key=True)


# =============================================================================
# Connection Types Models
# =============================================================================


class ConnTypeBase(SQLModel):
    """Base model for connection type definitions."""
    identifier: str = Field(max_length=32, index=True)
    description: str = Field(default="", max_length=128)
    min_upload: int = 0
    min_download: int = 0


class ConnType(ConnTypeBase, table=True):
    """Connection type definition."""
    __tablename__ = "conn_types"
    
    id: Optional[int] = Field(default=None, primary_key=True)


# =============================================================================
# Plugin Python Configuration
# =============================================================================


class PythonScriptBase(SQLModel):
    """Base model for Python script configuration."""
    script_name: str = Field(max_length=255, index=True)
    enabled: bool = True
    log_level: int = 0


class PythonScript(PythonScriptBase, table=True):
    """Python plugin script configuration."""
    __tablename__ = "pi_python"
    
    id: Optional[int] = Field(default=None, primary_key=True)


# =============================================================================
# Invite Code Models
# =============================================================================


class InviteCodeBase(SQLModel):
    """Base model for invite codes."""
    code: str = Field(max_length=64, index=True)
    created_by: str = Field(max_length=64, index=True)  # Nick of the user who owns the code
    max_class: int = Field(default=UserClass.REGISTERED)  # Max class this invite can grant
    used: bool = False
    used_by: Optional[str] = Field(default=None, max_length=64)
    used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: Optional[datetime] = None  # None = never expires


class InviteCode(InviteCodeBase, table=True):
    """Invite code for user registration."""
    __tablename__ = "invite_codes"

    id: Optional[int] = Field(default=None, primary_key=True)


class InviteCodeRead(InviteCodeBase):
    """Schema for reading an invite code."""
    id: int


class InviteCodeCreate(SQLModel):
    """Schema for creating invite codes (admin allocating to a user)."""
    nick: str  # User to allocate codes to
    count: int = Field(default=1, ge=1, le=100)  # How many codes to create
    max_class: int = Field(default=UserClass.REGISTERED)  # Max class these invites can grant


# =============================================================================
# Registration Models
# =============================================================================


class RegisterRequest(SQLModel):
    """Schema for public self-registration."""
    nick: str = Field(max_length=64)
    password: str = Field(max_length=128)
    invite_code: Optional[str] = Field(default=None, max_length=64)
