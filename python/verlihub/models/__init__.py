"""
SQLModel models for Verlihub database.

These models mirror the existing MySQL tables used by Verlihub,
allowing the Python layer to read/write hub data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Optional

from sqlalchemy import DateTime as SADateTime
from sqlmodel import Field, Relationship, SQLModel

# Timezone-aware datetime column type for PostgreSQL compatibility.
# asyncpg is strict: TIMESTAMP WITHOUT TIME ZONE rejects aware datetimes.
_TZDateTime = SADateTime(timezone=True)


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
    email: str = Field(default="", max_length=256)  # User email address
    login_last_ip: str = Field(default="", max_length=45)  # IPv4/IPv6
    login_last: Optional[datetime] = Field(default=None, sa_type=_TZDateTime)
    login_count: int = 0
    user_class: int = Field(default=UserClass.REGISTERED)
    user_class_ex: int = 0
    reg_date: datetime = Field(default_factory=utc_now, sa_type=_TZDateTime)
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
    email: Optional[str] = None
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
    date_start: datetime = Field(default_factory=utc_now, sa_type=_TZDateTime)
    date_limit: Optional[datetime] = Field(default=None, sa_type=_TZDateTime)
    nick_op: str = Field(default="", max_length=64)
    reason: str = Field(default="", max_length=512)
    last_hit: Optional[datetime] = Field(default=None, sa_type=_TZDateTime)
    this_kick: bool = False
    # IP range fields for subnet/CIDR bans (BanType.RANGE)
    ip_range_min: str = Field(default="", max_length=45)
    ip_range_max: str = Field(default="", max_length=45)
    cidr: str = Field(default="", max_length=49)  # e.g. "192.168.1.0/24"


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
# Penalty Models (temporary per-user restrictions)
# =============================================================================


class PenaltyType(IntEnum):
    """Penalty types — what capability is restricted."""
    GAG = 1       # Cannot send main chat
    NO_PM = 2     # Cannot send private messages
    NO_SEARCH = 4 # Cannot search
    NO_CTM = 8    # Cannot do file transfers
    NO_MYINFO = 16 # Cannot update $MyINFO


class PenaltyBase(SQLModel):
    """Base model for penalties."""
    nick: str = Field(max_length=64, index=True)
    ip: str = Field(default="", max_length=45)
    penalty_type: int = Field(default=PenaltyType.GAG)
    reason: str = Field(default="", max_length=512)
    op_nick: str = Field(default="", max_length=64)
    date_start: datetime = Field(default_factory=utc_now, sa_type=_TZDateTime)
    date_end: Optional[datetime] = Field(default=None, sa_type=_TZDateTime)


class Penalty(PenaltyBase, table=True):
    """Active penalty (temporary restriction) on a user."""
    __tablename__ = "penalties"

    id: Optional[int] = Field(default=None, primary_key=True)


class PenaltyCreate(PenaltyBase):
    """Schema for creating a penalty."""
    pass


class PenaltyRead(PenaltyBase):
    """Schema for reading a penalty."""
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


class TriggerCreate(TriggerBase):
    """Schema for creating a trigger."""
    pass


class TriggerRead(TriggerBase):
    """Schema for reading a trigger."""
    id: int


# =============================================================================
# Kick Models
# =============================================================================


class KickBase(SQLModel):
    """Base model for kicks (kick history)."""
    nick: str = Field(max_length=64, index=True)
    ip: str = Field(default="", max_length=45)
    op: str = Field(max_length=64, index=True)
    reason: str = Field(default="", max_length=512)
    time: datetime = Field(default_factory=utc_now, sa_type=_TZDateTime)
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


class RedirectCreate(RedirectBase):
    """Schema for creating a redirect."""
    pass


class RedirectRead(RedirectBase):
    """Schema for reading a redirect."""
    id: int


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


class DCClientCreate(DCClientBase):
    """Schema for creating a DC client rule."""
    pass


class DCClientRead(DCClientBase):
    """Schema for reading a DC client rule."""
    id: int


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
    used_at: Optional[datetime] = Field(default=None, sa_type=_TZDateTime)
    created_at: datetime = Field(default_factory=utc_now, sa_type=_TZDateTime)
    expires_at: Optional[datetime] = Field(default=None, sa_type=_TZDateTime)  # None = never expires


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
    email: Optional[str] = Field(default=None, max_length=256)
    invite_code: Optional[str] = Field(default=None, max_length=64)


# =============================================================================
# Hub List Entry Models (for hublist server feature)
# =============================================================================


class HubListEntryBase(SQLModel):
    """Base model for a hub registered on our hublist server."""
    name: str = Field(max_length=255, index=True)
    address: str = Field(max_length=512, index=True)  # dchub://host:port or adcs://host:port
    description: str = Field(default="", max_length=1024)
    users: int = Field(default=0)
    share: int = Field(default=0)  # Total share in bytes
    min_share: int = Field(default=0)  # Minimum share required (bytes)
    max_users: int = Field(default=0)
    country: str = Field(default="", max_length=2)  # Two-letter ISO
    encoding: str = Field(default="UTF-8", max_length=32)
    owner: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=256)
    website: str = Field(default="", max_length=512)
    logo: str = Field(default="", max_length=512)  # URL to hub icon/logo
    status: int = Field(default=1)  # 1=online
    software: str = Field(default="", max_length=128)
    # GeoIP enrichment (resolved server-side on registration)
    ip: str = Field(default="", max_length=45)
    hostname: str = Field(default="", max_length=256)
    city: str = Field(default="", max_length=128)
    asn: str = Field(default="", max_length=128)  # e.g. "AS13335 Cloudflare"
    last_seen: datetime = Field(default_factory=utc_now, sa_type=_TZDateTime)
    registered_at: datetime = Field(default_factory=utc_now, sa_type=_TZDateTime)


class HubListEntry(HubListEntryBase, table=True):
    """A hub registered on our hublist server."""
    __tablename__ = "hublist_entries"

    id: Optional[int] = Field(default=None, primary_key=True)


class HubListEntryRead(HubListEntryBase):
    """Schema for reading a hublist entry."""
    id: int


class HubListEntryCreate(SQLModel):
    """Schema for creating / updating a hublist entry via HTTP POST."""
    name: str = Field(max_length=255)
    address: str = Field(max_length=512)
    description: str = Field(default="", max_length=1024)
    users: int = Field(default=0, ge=0)
    share: int = Field(default=0, ge=0)
    min_share: int = Field(default=0, ge=0)
    max_users: int = Field(default=0, ge=0)
    country: str = Field(default="", max_length=2)
    encoding: str = Field(default="UTF-8", max_length=32)
    owner: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=256)
    website: str = Field(default="", max_length=512)
    logo: str = Field(default="", max_length=512)
    software: str = Field(default="", max_length=128)


# ---------------------------------------------------------------------------
# Block-level enum and models for hublist blocking
# ---------------------------------------------------------------------------

class HubListBlockType(str, Enum):
    """The level at which a hub is blocked from the hublist."""
    IP = "ip"
    HOSTNAME = "hostname"
    DOMAIN = "domain"
    ASN = "asn"
    CITY = "city"
    COUNTRY = "country"


class HubListBlockBase(SQLModel):
    """Base model for a hublist block rule."""
    block_type: HubListBlockType = Field(index=True)
    value: str = Field(max_length=512, index=True)       # e.g. IP, hostname, "AS13335", "DE"
    reason: str = Field(default="", max_length=1024)
    created_by: str = Field(default="", max_length=128)   # nick of creator
    created_at: datetime = Field(default_factory=utc_now, sa_type=_TZDateTime)
    expires_at: Optional[datetime] = Field(default=None, sa_type=_TZDateTime)


class HubListBlock(HubListBlockBase, table=True):  # type: ignore[call-arg]
    """Persisted hublist block rule."""
    __tablename__ = "hublist_blocks"

    id: Optional[int] = Field(default=None, primary_key=True)


class HubListBlockRead(HubListBlockBase):
    """Schema returned via API."""
    id: int


class HubListBlockCreate(SQLModel):
    """Schema for creating a new block rule."""
    block_type: HubListBlockType
    value: str = Field(max_length=512)
    reason: str = Field(default="", max_length=1024)
    expires_at: Optional[datetime] = None


# =============================================================================
# Bot Memory (persistent notes for LLM bot)
# =============================================================================


class BotNoteBase(SQLModel):
    """Base model for bot memory notes."""
    topic: str = Field(max_length=255, index=True)
    content: str = Field(default="", max_length=4096)
    mood: str = Field(default="", max_length=64)  # mood name when note was saved
    created_at: datetime = Field(default_factory=utc_now, sa_type=_TZDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=_TZDateTime)


class BotNote(BotNoteBase, table=True):
    """Persistent note stored by the LLM bot."""
    __tablename__ = "bot_notes"

    id: Optional[int] = Field(default=None, primary_key=True)


class BotNoteRead(BotNoteBase):
    """Schema for reading a bot note."""
    id: int
