from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PbEnvelope(_message.Message):
    __slots__ = ("route", "from_nick", "to_nick", "features", "timestamp", "sequence", "chat", "user_info", "search", "search_result", "connect", "hub_info", "user_list", "relay_request", "relay_ack", "relay_data", "relay_closed", "relay_status", "pm_key_exchange", "encrypted_pm", "pm_session_end", "status", "extension", "media_upload", "media_meta", "media_delete", "media_capabilities", "call_offer", "call_answer", "call_candidate", "call_end", "call_media_control", "hub_stream")
    class RouteType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        BROADCAST: _ClassVar[PbEnvelope.RouteType]
        DIRECT: _ClassVar[PbEnvelope.RouteType]
        HUB: _ClassVar[PbEnvelope.RouteType]
        INFO: _ClassVar[PbEnvelope.RouteType]
        ECHO: _ClassVar[PbEnvelope.RouteType]
        FEATURE: _ClassVar[PbEnvelope.RouteType]
    BROADCAST: PbEnvelope.RouteType
    DIRECT: PbEnvelope.RouteType
    HUB: PbEnvelope.RouteType
    INFO: PbEnvelope.RouteType
    ECHO: PbEnvelope.RouteType
    FEATURE: PbEnvelope.RouteType
    ROUTE_FIELD_NUMBER: _ClassVar[int]
    FROM_NICK_FIELD_NUMBER: _ClassVar[int]
    TO_NICK_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    CHAT_FIELD_NUMBER: _ClassVar[int]
    USER_INFO_FIELD_NUMBER: _ClassVar[int]
    SEARCH_FIELD_NUMBER: _ClassVar[int]
    SEARCH_RESULT_FIELD_NUMBER: _ClassVar[int]
    CONNECT_FIELD_NUMBER: _ClassVar[int]
    HUB_INFO_FIELD_NUMBER: _ClassVar[int]
    USER_LIST_FIELD_NUMBER: _ClassVar[int]
    RELAY_REQUEST_FIELD_NUMBER: _ClassVar[int]
    RELAY_ACK_FIELD_NUMBER: _ClassVar[int]
    RELAY_DATA_FIELD_NUMBER: _ClassVar[int]
    RELAY_CLOSED_FIELD_NUMBER: _ClassVar[int]
    RELAY_STATUS_FIELD_NUMBER: _ClassVar[int]
    PM_KEY_EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    ENCRYPTED_PM_FIELD_NUMBER: _ClassVar[int]
    PM_SESSION_END_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EXTENSION_FIELD_NUMBER: _ClassVar[int]
    MEDIA_UPLOAD_FIELD_NUMBER: _ClassVar[int]
    MEDIA_META_FIELD_NUMBER: _ClassVar[int]
    MEDIA_DELETE_FIELD_NUMBER: _ClassVar[int]
    MEDIA_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    CALL_OFFER_FIELD_NUMBER: _ClassVar[int]
    CALL_ANSWER_FIELD_NUMBER: _ClassVar[int]
    CALL_CANDIDATE_FIELD_NUMBER: _ClassVar[int]
    CALL_END_FIELD_NUMBER: _ClassVar[int]
    CALL_MEDIA_CONTROL_FIELD_NUMBER: _ClassVar[int]
    HUB_STREAM_FIELD_NUMBER: _ClassVar[int]
    route: PbEnvelope.RouteType
    from_nick: str
    to_nick: str
    features: str
    timestamp: int
    sequence: int
    chat: PbChat
    user_info: PbUserInfo
    search: PbSearch
    search_result: PbSearchResult
    connect: PbConnect
    hub_info: PbHubInfo
    user_list: PbUserList
    relay_request: PbRelayRequest
    relay_ack: PbRelayAck
    relay_data: PbRelayData
    relay_closed: PbRelayClosed
    relay_status: PbRelayStatus
    pm_key_exchange: PbPMKeyExchange
    encrypted_pm: PbEncryptedPM
    pm_session_end: PbPMSessionEnd
    status: PbStatus
    extension: PbExtension
    media_upload: PbMediaUpload
    media_meta: PbMediaMeta
    media_delete: PbMediaDelete
    media_capabilities: PbMediaCapabilities
    call_offer: PbCallOffer
    call_answer: PbCallAnswer
    call_candidate: PbCallCandidate
    call_end: PbCallEnd
    call_media_control: PbCallMediaControl
    hub_stream: PbHubStream
    def __init__(self, route: _Optional[_Union[PbEnvelope.RouteType, str]] = ..., from_nick: _Optional[str] = ..., to_nick: _Optional[str] = ..., features: _Optional[str] = ..., timestamp: _Optional[int] = ..., sequence: _Optional[int] = ..., chat: _Optional[_Union[PbChat, _Mapping]] = ..., user_info: _Optional[_Union[PbUserInfo, _Mapping]] = ..., search: _Optional[_Union[PbSearch, _Mapping]] = ..., search_result: _Optional[_Union[PbSearchResult, _Mapping]] = ..., connect: _Optional[_Union[PbConnect, _Mapping]] = ..., hub_info: _Optional[_Union[PbHubInfo, _Mapping]] = ..., user_list: _Optional[_Union[PbUserList, _Mapping]] = ..., relay_request: _Optional[_Union[PbRelayRequest, _Mapping]] = ..., relay_ack: _Optional[_Union[PbRelayAck, _Mapping]] = ..., relay_data: _Optional[_Union[PbRelayData, _Mapping]] = ..., relay_closed: _Optional[_Union[PbRelayClosed, _Mapping]] = ..., relay_status: _Optional[_Union[PbRelayStatus, _Mapping]] = ..., pm_key_exchange: _Optional[_Union[PbPMKeyExchange, _Mapping]] = ..., encrypted_pm: _Optional[_Union[PbEncryptedPM, _Mapping]] = ..., pm_session_end: _Optional[_Union[PbPMSessionEnd, _Mapping]] = ..., status: _Optional[_Union[PbStatus, _Mapping]] = ..., extension: _Optional[_Union[PbExtension, _Mapping]] = ..., media_upload: _Optional[_Union[PbMediaUpload, _Mapping]] = ..., media_meta: _Optional[_Union[PbMediaMeta, _Mapping]] = ..., media_delete: _Optional[_Union[PbMediaDelete, _Mapping]] = ..., media_capabilities: _Optional[_Union[PbMediaCapabilities, _Mapping]] = ..., call_offer: _Optional[_Union[PbCallOffer, _Mapping]] = ..., call_answer: _Optional[_Union[PbCallAnswer, _Mapping]] = ..., call_candidate: _Optional[_Union[PbCallCandidate, _Mapping]] = ..., call_end: _Optional[_Union[PbCallEnd, _Mapping]] = ..., call_media_control: _Optional[_Union[PbCallMediaControl, _Mapping]] = ..., hub_stream: _Optional[_Union[PbHubStream, _Mapping]] = ...) -> None: ...

class PbChat(_message.Message):
    __slots__ = ("text", "is_action", "target_nick", "is_pm", "attachments")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    IS_ACTION_FIELD_NUMBER: _ClassVar[int]
    TARGET_NICK_FIELD_NUMBER: _ClassVar[int]
    IS_PM_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENTS_FIELD_NUMBER: _ClassVar[int]
    text: str
    is_action: bool
    target_nick: str
    is_pm: bool
    attachments: _containers.RepeatedCompositeFieldContainer[PbMediaRef]
    def __init__(self, text: _Optional[str] = ..., is_action: bool = ..., target_nick: _Optional[str] = ..., is_pm: bool = ..., attachments: _Optional[_Iterable[_Union[PbMediaRef, _Mapping]]] = ...) -> None: ...

class PbUserInfo(_message.Message):
    __slots__ = ("nick", "description", "tag", "email", "share_size", "shared_files", "upload_slots", "free_slots", "connection_speed", "upload_speed_bps", "download_speed_bps", "ipv4", "ipv6", "udp_port", "tcp_port", "is_passive", "supports_tls", "tls_keyprint", "user_class", "is_away", "hub_url", "client_id", "features", "extra")
    class UserClass(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        USER: _ClassVar[PbUserInfo.UserClass]
        REGISTERED: _ClassVar[PbUserInfo.UserClass]
        VIP: _ClassVar[PbUserInfo.UserClass]
        OPERATOR: _ClassVar[PbUserInfo.UserClass]
        CHEEF: _ClassVar[PbUserInfo.UserClass]
        ADMIN: _ClassVar[PbUserInfo.UserClass]
        MASTER: _ClassVar[PbUserInfo.UserClass]
    USER: PbUserInfo.UserClass
    REGISTERED: PbUserInfo.UserClass
    VIP: PbUserInfo.UserClass
    OPERATOR: PbUserInfo.UserClass
    CHEEF: PbUserInfo.UserClass
    ADMIN: PbUserInfo.UserClass
    MASTER: PbUserInfo.UserClass
    class ExtraEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    NICK_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAG_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    SHARE_SIZE_FIELD_NUMBER: _ClassVar[int]
    SHARED_FILES_FIELD_NUMBER: _ClassVar[int]
    UPLOAD_SLOTS_FIELD_NUMBER: _ClassVar[int]
    FREE_SLOTS_FIELD_NUMBER: _ClassVar[int]
    CONNECTION_SPEED_FIELD_NUMBER: _ClassVar[int]
    UPLOAD_SPEED_BPS_FIELD_NUMBER: _ClassVar[int]
    DOWNLOAD_SPEED_BPS_FIELD_NUMBER: _ClassVar[int]
    IPV4_FIELD_NUMBER: _ClassVar[int]
    IPV6_FIELD_NUMBER: _ClassVar[int]
    UDP_PORT_FIELD_NUMBER: _ClassVar[int]
    TCP_PORT_FIELD_NUMBER: _ClassVar[int]
    IS_PASSIVE_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_TLS_FIELD_NUMBER: _ClassVar[int]
    TLS_KEYPRINT_FIELD_NUMBER: _ClassVar[int]
    USER_CLASS_FIELD_NUMBER: _ClassVar[int]
    IS_AWAY_FIELD_NUMBER: _ClassVar[int]
    HUB_URL_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    EXTRA_FIELD_NUMBER: _ClassVar[int]
    nick: str
    description: str
    tag: str
    email: str
    share_size: int
    shared_files: int
    upload_slots: int
    free_slots: int
    connection_speed: str
    upload_speed_bps: int
    download_speed_bps: int
    ipv4: str
    ipv6: str
    udp_port: int
    tcp_port: int
    is_passive: bool
    supports_tls: bool
    tls_keyprint: str
    user_class: PbUserInfo.UserClass
    is_away: bool
    hub_url: str
    client_id: str
    features: _containers.RepeatedScalarFieldContainer[str]
    extra: _containers.ScalarMap[str, str]
    def __init__(self, nick: _Optional[str] = ..., description: _Optional[str] = ..., tag: _Optional[str] = ..., email: _Optional[str] = ..., share_size: _Optional[int] = ..., shared_files: _Optional[int] = ..., upload_slots: _Optional[int] = ..., free_slots: _Optional[int] = ..., connection_speed: _Optional[str] = ..., upload_speed_bps: _Optional[int] = ..., download_speed_bps: _Optional[int] = ..., ipv4: _Optional[str] = ..., ipv6: _Optional[str] = ..., udp_port: _Optional[int] = ..., tcp_port: _Optional[int] = ..., is_passive: bool = ..., supports_tls: bool = ..., tls_keyprint: _Optional[str] = ..., user_class: _Optional[_Union[PbUserInfo.UserClass, str]] = ..., is_away: bool = ..., hub_url: _Optional[str] = ..., client_id: _Optional[str] = ..., features: _Optional[_Iterable[str]] = ..., extra: _Optional[_Mapping[str, str]] = ...) -> None: ...

class PbSearch(_message.Message):
    __slots__ = ("query", "exclude", "file_type", "size_mode", "size", "tth", "token", "extensions", "max_results")
    class FileType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        ANY: _ClassVar[PbSearch.FileType]
        AUDIO: _ClassVar[PbSearch.FileType]
        COMPRESSED: _ClassVar[PbSearch.FileType]
        DOCUMENT: _ClassVar[PbSearch.FileType]
        EXECUTABLE: _ClassVar[PbSearch.FileType]
        PICTURE: _ClassVar[PbSearch.FileType]
        VIDEO: _ClassVar[PbSearch.FileType]
        DIRECTORY: _ClassVar[PbSearch.FileType]
        TTH: _ClassVar[PbSearch.FileType]
    ANY: PbSearch.FileType
    AUDIO: PbSearch.FileType
    COMPRESSED: PbSearch.FileType
    DOCUMENT: PbSearch.FileType
    EXECUTABLE: PbSearch.FileType
    PICTURE: PbSearch.FileType
    VIDEO: PbSearch.FileType
    DIRECTORY: PbSearch.FileType
    TTH: PbSearch.FileType
    class SizeMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        NONE: _ClassVar[PbSearch.SizeMode]
        AT_LEAST: _ClassVar[PbSearch.SizeMode]
        AT_MOST: _ClassVar[PbSearch.SizeMode]
        EXACT: _ClassVar[PbSearch.SizeMode]
    NONE: PbSearch.SizeMode
    AT_LEAST: PbSearch.SizeMode
    AT_MOST: PbSearch.SizeMode
    EXACT: PbSearch.SizeMode
    QUERY_FIELD_NUMBER: _ClassVar[int]
    EXCLUDE_FIELD_NUMBER: _ClassVar[int]
    FILE_TYPE_FIELD_NUMBER: _ClassVar[int]
    SIZE_MODE_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    TTH_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    MAX_RESULTS_FIELD_NUMBER: _ClassVar[int]
    query: str
    exclude: _containers.RepeatedScalarFieldContainer[str]
    file_type: PbSearch.FileType
    size_mode: PbSearch.SizeMode
    size: int
    tth: str
    token: str
    extensions: _containers.RepeatedScalarFieldContainer[str]
    max_results: int
    def __init__(self, query: _Optional[str] = ..., exclude: _Optional[_Iterable[str]] = ..., file_type: _Optional[_Union[PbSearch.FileType, str]] = ..., size_mode: _Optional[_Union[PbSearch.SizeMode, str]] = ..., size: _Optional[int] = ..., tth: _Optional[str] = ..., token: _Optional[str] = ..., extensions: _Optional[_Iterable[str]] = ..., max_results: _Optional[int] = ...) -> None: ...

class PbSearchResult(_message.Message):
    __slots__ = ("nick", "filename", "size", "free_slots", "total_slots", "tth", "hub_name", "hub_url", "token", "is_directory", "path")
    NICK_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    FREE_SLOTS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_SLOTS_FIELD_NUMBER: _ClassVar[int]
    TTH_FIELD_NUMBER: _ClassVar[int]
    HUB_NAME_FIELD_NUMBER: _ClassVar[int]
    HUB_URL_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    IS_DIRECTORY_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    nick: str
    filename: str
    size: int
    free_slots: int
    total_slots: int
    tth: str
    hub_name: str
    hub_url: str
    token: str
    is_directory: bool
    path: str
    def __init__(self, nick: _Optional[str] = ..., filename: _Optional[str] = ..., size: _Optional[int] = ..., free_slots: _Optional[int] = ..., total_slots: _Optional[int] = ..., tth: _Optional[str] = ..., hub_name: _Optional[str] = ..., hub_url: _Optional[str] = ..., token: _Optional[str] = ..., is_directory: bool = ..., path: _Optional[str] = ...) -> None: ...

class PbConnect(_message.Message):
    __slots__ = ("type", "target_nick", "ip", "port", "use_tls", "token", "protocol")
    class ConnectType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        CONNECT_TO_ME: _ClassVar[PbConnect.ConnectType]
        REV_CONNECT_TO_ME: _ClassVar[PbConnect.ConnectType]
        NAT_TRAVERSAL: _ClassVar[PbConnect.ConnectType]
        HUB_RELAY: _ClassVar[PbConnect.ConnectType]
    CONNECT_TO_ME: PbConnect.ConnectType
    REV_CONNECT_TO_ME: PbConnect.ConnectType
    NAT_TRAVERSAL: PbConnect.ConnectType
    HUB_RELAY: PbConnect.ConnectType
    TYPE_FIELD_NUMBER: _ClassVar[int]
    TARGET_NICK_FIELD_NUMBER: _ClassVar[int]
    IP_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    USE_TLS_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_FIELD_NUMBER: _ClassVar[int]
    type: PbConnect.ConnectType
    target_nick: str
    ip: str
    port: int
    use_tls: bool
    token: str
    protocol: str
    def __init__(self, type: _Optional[_Union[PbConnect.ConnectType, str]] = ..., target_nick: _Optional[str] = ..., ip: _Optional[str] = ..., port: _Optional[int] = ..., use_tls: bool = ..., token: _Optional[str] = ..., protocol: _Optional[str] = ...) -> None: ...

class PbHubInfo(_message.Message):
    __slots__ = ("name", "topic", "description", "user_count", "total_share", "min_share", "max_users", "hub_url", "failover_urls", "encoding", "rules", "protocol_version", "session_token")
    class RulesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    NAME_FIELD_NUMBER: _ClassVar[int]
    TOPIC_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    USER_COUNT_FIELD_NUMBER: _ClassVar[int]
    TOTAL_SHARE_FIELD_NUMBER: _ClassVar[int]
    MIN_SHARE_FIELD_NUMBER: _ClassVar[int]
    MAX_USERS_FIELD_NUMBER: _ClassVar[int]
    HUB_URL_FIELD_NUMBER: _ClassVar[int]
    FAILOVER_URLS_FIELD_NUMBER: _ClassVar[int]
    ENCODING_FIELD_NUMBER: _ClassVar[int]
    RULES_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    SESSION_TOKEN_FIELD_NUMBER: _ClassVar[int]
    name: str
    topic: str
    description: str
    user_count: int
    total_share: int
    min_share: int
    max_users: int
    hub_url: str
    failover_urls: _containers.RepeatedScalarFieldContainer[str]
    encoding: str
    rules: _containers.ScalarMap[str, str]
    protocol_version: int
    session_token: str
    def __init__(self, name: _Optional[str] = ..., topic: _Optional[str] = ..., description: _Optional[str] = ..., user_count: _Optional[int] = ..., total_share: _Optional[int] = ..., min_share: _Optional[int] = ..., max_users: _Optional[int] = ..., hub_url: _Optional[str] = ..., failover_urls: _Optional[_Iterable[str]] = ..., encoding: _Optional[str] = ..., rules: _Optional[_Mapping[str, str]] = ..., protocol_version: _Optional[int] = ..., session_token: _Optional[str] = ...) -> None: ...

class PbUserList(_message.Message):
    __slots__ = ("users", "is_full", "removed_nicks")
    USERS_FIELD_NUMBER: _ClassVar[int]
    IS_FULL_FIELD_NUMBER: _ClassVar[int]
    REMOVED_NICKS_FIELD_NUMBER: _ClassVar[int]
    users: _containers.RepeatedCompositeFieldContainer[PbUserInfo]
    is_full: bool
    removed_nicks: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, users: _Optional[_Iterable[_Union[PbUserInfo, _Mapping]]] = ..., is_full: bool = ..., removed_nicks: _Optional[_Iterable[str]] = ...) -> None: ...

class PbStatus(_message.Message):
    __slots__ = ("code", "severity", "message", "detail")
    class Severity(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        INFO: _ClassVar[PbStatus.Severity]
        WARNING: _ClassVar[PbStatus.Severity]
        ERROR: _ClassVar[PbStatus.Severity]
        FATAL: _ClassVar[PbStatus.Severity]
    INFO: PbStatus.Severity
    WARNING: PbStatus.Severity
    ERROR: PbStatus.Severity
    FATAL: PbStatus.Severity
    CODE_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    code: int
    severity: PbStatus.Severity
    message: str
    detail: str
    def __init__(self, code: _Optional[int] = ..., severity: _Optional[_Union[PbStatus.Severity, str]] = ..., message: _Optional[str] = ..., detail: _Optional[str] = ...) -> None: ...

class PbExtension(_message.Message):
    __slots__ = ("type_url", "payload")
    TYPE_URL_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    type_url: str
    payload: bytes
    def __init__(self, type_url: _Optional[str] = ..., payload: _Optional[bytes] = ...) -> None: ...

class PbRelayRequest(_message.Message):
    __slots__ = ("target_nick", "token", "public_key", "purpose", "estimated_size")
    class RelayPurpose(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        FILE_TRANSFER: _ClassVar[PbRelayRequest.RelayPurpose]
        FILE_LIST: _ClassVar[PbRelayRequest.RelayPurpose]
        STREAM: _ClassVar[PbRelayRequest.RelayPurpose]
        GENERIC: _ClassVar[PbRelayRequest.RelayPurpose]
    FILE_TRANSFER: PbRelayRequest.RelayPurpose
    FILE_LIST: PbRelayRequest.RelayPurpose
    STREAM: PbRelayRequest.RelayPurpose
    GENERIC: PbRelayRequest.RelayPurpose
    TARGET_NICK_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    PUBLIC_KEY_FIELD_NUMBER: _ClassVar[int]
    PURPOSE_FIELD_NUMBER: _ClassVar[int]
    ESTIMATED_SIZE_FIELD_NUMBER: _ClassVar[int]
    target_nick: str
    token: str
    public_key: bytes
    purpose: PbRelayRequest.RelayPurpose
    estimated_size: int
    def __init__(self, target_nick: _Optional[str] = ..., token: _Optional[str] = ..., public_key: _Optional[bytes] = ..., purpose: _Optional[_Union[PbRelayRequest.RelayPurpose, str]] = ..., estimated_size: _Optional[int] = ...) -> None: ...

class PbRelayAck(_message.Message):
    __slots__ = ("token", "accepted", "public_key", "relay_id", "reject_reason")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    PUBLIC_KEY_FIELD_NUMBER: _ClassVar[int]
    RELAY_ID_FIELD_NUMBER: _ClassVar[int]
    REJECT_REASON_FIELD_NUMBER: _ClassVar[int]
    token: str
    accepted: bool
    public_key: bytes
    relay_id: int
    reject_reason: str
    def __init__(self, token: _Optional[str] = ..., accepted: bool = ..., public_key: _Optional[bytes] = ..., relay_id: _Optional[int] = ..., reject_reason: _Optional[str] = ...) -> None: ...

class PbRelayData(_message.Message):
    __slots__ = ("relay_id", "data", "offset")
    RELAY_ID_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    relay_id: int
    data: bytes
    offset: int
    def __init__(self, relay_id: _Optional[int] = ..., data: _Optional[bytes] = ..., offset: _Optional[int] = ...) -> None: ...

class PbRelayClosed(_message.Message):
    __slots__ = ("relay_id", "reason")
    class CloseReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        NORMAL: _ClassVar[PbRelayClosed.CloseReason]
        ERROR: _ClassVar[PbRelayClosed.CloseReason]
        TIMEOUT: _ClassVar[PbRelayClosed.CloseReason]
        HUB_LIMIT: _ClassVar[PbRelayClosed.CloseReason]
        USER_DISCONNECT: _ClassVar[PbRelayClosed.CloseReason]
    NORMAL: PbRelayClosed.CloseReason
    ERROR: PbRelayClosed.CloseReason
    TIMEOUT: PbRelayClosed.CloseReason
    HUB_LIMIT: PbRelayClosed.CloseReason
    USER_DISCONNECT: PbRelayClosed.CloseReason
    RELAY_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    relay_id: int
    reason: PbRelayClosed.CloseReason
    def __init__(self, relay_id: _Optional[int] = ..., reason: _Optional[_Union[PbRelayClosed.CloseReason, str]] = ...) -> None: ...

class PbRelayStatus(_message.Message):
    __slots__ = ("relay_id", "bytes_relayed", "active_sessions", "max_sessions", "bandwidth_used", "bandwidth_limit")
    RELAY_ID_FIELD_NUMBER: _ClassVar[int]
    BYTES_RELAYED_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    MAX_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    BANDWIDTH_USED_FIELD_NUMBER: _ClassVar[int]
    BANDWIDTH_LIMIT_FIELD_NUMBER: _ClassVar[int]
    relay_id: int
    bytes_relayed: int
    active_sessions: int
    max_sessions: int
    bandwidth_used: int
    bandwidth_limit: int
    def __init__(self, relay_id: _Optional[int] = ..., bytes_relayed: _Optional[int] = ..., active_sessions: _Optional[int] = ..., max_sessions: _Optional[int] = ..., bandwidth_used: _Optional[int] = ..., bandwidth_limit: _Optional[int] = ...) -> None: ...

class PbPMKeyExchange(_message.Message):
    __slots__ = ("target_nick", "public_key", "key_signature", "key_fingerprint", "protocol_version")
    TARGET_NICK_FIELD_NUMBER: _ClassVar[int]
    PUBLIC_KEY_FIELD_NUMBER: _ClassVar[int]
    KEY_SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    KEY_FINGERPRINT_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    target_nick: str
    public_key: bytes
    key_signature: bytes
    key_fingerprint: str
    protocol_version: int
    def __init__(self, target_nick: _Optional[str] = ..., public_key: _Optional[bytes] = ..., key_signature: _Optional[bytes] = ..., key_fingerprint: _Optional[str] = ..., protocol_version: _Optional[int] = ...) -> None: ...

class PbEncryptedPM(_message.Message):
    __slots__ = ("target_nick", "nonce", "ciphertext", "sender_pubkey_hint")
    TARGET_NICK_FIELD_NUMBER: _ClassVar[int]
    NONCE_FIELD_NUMBER: _ClassVar[int]
    CIPHERTEXT_FIELD_NUMBER: _ClassVar[int]
    SENDER_PUBKEY_HINT_FIELD_NUMBER: _ClassVar[int]
    target_nick: str
    nonce: int
    ciphertext: bytes
    sender_pubkey_hint: bytes
    def __init__(self, target_nick: _Optional[str] = ..., nonce: _Optional[int] = ..., ciphertext: _Optional[bytes] = ..., sender_pubkey_hint: _Optional[bytes] = ...) -> None: ...

class PbPMPlaintext(_message.Message):
    __slots__ = ("text", "is_action", "timestamp", "reply_to_hash", "extra", "encrypted_attachments")
    class ExtraEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    TEXT_FIELD_NUMBER: _ClassVar[int]
    IS_ACTION_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    REPLY_TO_HASH_FIELD_NUMBER: _ClassVar[int]
    EXTRA_FIELD_NUMBER: _ClassVar[int]
    ENCRYPTED_ATTACHMENTS_FIELD_NUMBER: _ClassVar[int]
    text: str
    is_action: bool
    timestamp: int
    reply_to_hash: str
    extra: _containers.ScalarMap[str, str]
    encrypted_attachments: _containers.RepeatedCompositeFieldContainer[PbEncryptedMediaRef]
    def __init__(self, text: _Optional[str] = ..., is_action: bool = ..., timestamp: _Optional[int] = ..., reply_to_hash: _Optional[str] = ..., extra: _Optional[_Mapping[str, str]] = ..., encrypted_attachments: _Optional[_Iterable[_Union[PbEncryptedMediaRef, _Mapping]]] = ...) -> None: ...

class PbPMSessionEnd(_message.Message):
    __slots__ = ("target_nick", "reason")
    class Reason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        NORMAL_CLOSE: _ClassVar[PbPMSessionEnd.Reason]
        KEY_ROTATION: _ClassVar[PbPMSessionEnd.Reason]
        SECURITY_ALERT: _ClassVar[PbPMSessionEnd.Reason]
    NORMAL_CLOSE: PbPMSessionEnd.Reason
    KEY_ROTATION: PbPMSessionEnd.Reason
    SECURITY_ALERT: PbPMSessionEnd.Reason
    TARGET_NICK_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    target_nick: str
    reason: PbPMSessionEnd.Reason
    def __init__(self, target_nick: _Optional[str] = ..., reason: _Optional[_Union[PbPMSessionEnd.Reason, str]] = ...) -> None: ...

class PbMediaUpload(_message.Message):
    __slots__ = ("filename", "mime_type", "size", "requested_ttl", "is_encrypted", "checksum_sha256")
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_TTL_FIELD_NUMBER: _ClassVar[int]
    IS_ENCRYPTED_FIELD_NUMBER: _ClassVar[int]
    CHECKSUM_SHA256_FIELD_NUMBER: _ClassVar[int]
    filename: str
    mime_type: str
    size: int
    requested_ttl: int
    is_encrypted: bool
    checksum_sha256: str
    def __init__(self, filename: _Optional[str] = ..., mime_type: _Optional[str] = ..., size: _Optional[int] = ..., requested_ttl: _Optional[int] = ..., is_encrypted: bool = ..., checksum_sha256: _Optional[str] = ...) -> None: ...

class PbMediaMeta(_message.Message):
    __slots__ = ("media_id", "url", "thumbnail_url", "mime_type", "size", "filename", "expires_at", "uploader_nick", "width", "height", "duration_ms", "checksum_sha256")
    MEDIA_ID_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    THUMBNAIL_URL_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    UPLOADER_NICK_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    CHECKSUM_SHA256_FIELD_NUMBER: _ClassVar[int]
    media_id: str
    url: str
    thumbnail_url: str
    mime_type: str
    size: int
    filename: str
    expires_at: int
    uploader_nick: str
    width: int
    height: int
    duration_ms: int
    checksum_sha256: str
    def __init__(self, media_id: _Optional[str] = ..., url: _Optional[str] = ..., thumbnail_url: _Optional[str] = ..., mime_type: _Optional[str] = ..., size: _Optional[int] = ..., filename: _Optional[str] = ..., expires_at: _Optional[int] = ..., uploader_nick: _Optional[str] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., duration_ms: _Optional[int] = ..., checksum_sha256: _Optional[str] = ...) -> None: ...

class PbMediaDelete(_message.Message):
    __slots__ = ("media_id", "reason")
    MEDIA_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    media_id: str
    reason: str
    def __init__(self, media_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class PbMediaRef(_message.Message):
    __slots__ = ("media_id", "url", "thumbnail_url", "mime_type", "filename", "size", "width", "height", "duration_ms")
    MEDIA_ID_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    THUMBNAIL_URL_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    media_id: str
    url: str
    thumbnail_url: str
    mime_type: str
    filename: str
    size: int
    width: int
    height: int
    duration_ms: int
    def __init__(self, media_id: _Optional[str] = ..., url: _Optional[str] = ..., thumbnail_url: _Optional[str] = ..., mime_type: _Optional[str] = ..., filename: _Optional[str] = ..., size: _Optional[int] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., duration_ms: _Optional[int] = ...) -> None: ...

class PbEncryptedMediaRef(_message.Message):
    __slots__ = ("media_id", "url", "filename", "mime_type", "size", "file_encryption_key", "file_nonce", "width", "height", "duration_ms")
    MEDIA_ID_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    FILE_ENCRYPTION_KEY_FIELD_NUMBER: _ClassVar[int]
    FILE_NONCE_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    media_id: str
    url: str
    filename: str
    mime_type: str
    size: int
    file_encryption_key: bytes
    file_nonce: bytes
    width: int
    height: int
    duration_ms: int
    def __init__(self, media_id: _Optional[str] = ..., url: _Optional[str] = ..., filename: _Optional[str] = ..., mime_type: _Optional[str] = ..., size: _Optional[int] = ..., file_encryption_key: _Optional[bytes] = ..., file_nonce: _Optional[bytes] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., duration_ms: _Optional[int] = ...) -> None: ...

class PbMediaCapabilities(_message.Message):
    __slots__ = ("enabled", "max_file_size", "user_quota_remaining", "max_ttl", "default_ttl", "allowed_types", "thumbnails_available", "upload_url")
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    MAX_FILE_SIZE_FIELD_NUMBER: _ClassVar[int]
    USER_QUOTA_REMAINING_FIELD_NUMBER: _ClassVar[int]
    MAX_TTL_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_TTL_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_TYPES_FIELD_NUMBER: _ClassVar[int]
    THUMBNAILS_AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    UPLOAD_URL_FIELD_NUMBER: _ClassVar[int]
    enabled: bool
    max_file_size: int
    user_quota_remaining: int
    max_ttl: int
    default_ttl: int
    allowed_types: _containers.RepeatedScalarFieldContainer[str]
    thumbnails_available: bool
    upload_url: str
    def __init__(self, enabled: bool = ..., max_file_size: _Optional[int] = ..., user_quota_remaining: _Optional[int] = ..., max_ttl: _Optional[int] = ..., default_ttl: _Optional[int] = ..., allowed_types: _Optional[_Iterable[str]] = ..., thumbnails_available: bool = ..., upload_url: _Optional[str] = ...) -> None: ...

class PbCallOffer(_message.Message):
    __slots__ = ("target_nick", "call_id", "is_group", "media", "codecs", "group_id")
    class MediaType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        AUDIO: _ClassVar[PbCallOffer.MediaType]
        VIDEO: _ClassVar[PbCallOffer.MediaType]
        SCREEN_SHARE: _ClassVar[PbCallOffer.MediaType]
    AUDIO: PbCallOffer.MediaType
    VIDEO: PbCallOffer.MediaType
    SCREEN_SHARE: PbCallOffer.MediaType
    TARGET_NICK_FIELD_NUMBER: _ClassVar[int]
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    IS_GROUP_FIELD_NUMBER: _ClassVar[int]
    MEDIA_FIELD_NUMBER: _ClassVar[int]
    CODECS_FIELD_NUMBER: _ClassVar[int]
    GROUP_ID_FIELD_NUMBER: _ClassVar[int]
    target_nick: str
    call_id: str
    is_group: bool
    media: _containers.RepeatedScalarFieldContainer[PbCallOffer.MediaType]
    codecs: _containers.RepeatedCompositeFieldContainer[CodecInfo]
    group_id: str
    def __init__(self, target_nick: _Optional[str] = ..., call_id: _Optional[str] = ..., is_group: bool = ..., media: _Optional[_Iterable[_Union[PbCallOffer.MediaType, str]]] = ..., codecs: _Optional[_Iterable[_Union[CodecInfo, _Mapping]]] = ..., group_id: _Optional[str] = ...) -> None: ...

class PbCallAnswer(_message.Message):
    __slots__ = ("call_id", "accepted", "codecs", "reject_reason")
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    CODECS_FIELD_NUMBER: _ClassVar[int]
    REJECT_REASON_FIELD_NUMBER: _ClassVar[int]
    call_id: str
    accepted: bool
    codecs: _containers.RepeatedCompositeFieldContainer[CodecInfo]
    reject_reason: str
    def __init__(self, call_id: _Optional[str] = ..., accepted: bool = ..., codecs: _Optional[_Iterable[_Union[CodecInfo, _Mapping]]] = ..., reject_reason: _Optional[str] = ...) -> None: ...

class CodecInfo(_message.Message):
    __slots__ = ("name", "clock_rate", "channels", "bitrate", "params")
    class ParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    NAME_FIELD_NUMBER: _ClassVar[int]
    CLOCK_RATE_FIELD_NUMBER: _ClassVar[int]
    CHANNELS_FIELD_NUMBER: _ClassVar[int]
    BITRATE_FIELD_NUMBER: _ClassVar[int]
    PARAMS_FIELD_NUMBER: _ClassVar[int]
    name: str
    clock_rate: int
    channels: int
    bitrate: int
    params: _containers.ScalarMap[str, str]
    def __init__(self, name: _Optional[str] = ..., clock_rate: _Optional[int] = ..., channels: _Optional[int] = ..., bitrate: _Optional[int] = ..., params: _Optional[_Mapping[str, str]] = ...) -> None: ...

class PbCallCandidate(_message.Message):
    __slots__ = ("call_id", "candidate")
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    CANDIDATE_FIELD_NUMBER: _ClassVar[int]
    call_id: str
    candidate: str
    def __init__(self, call_id: _Optional[str] = ..., candidate: _Optional[str] = ...) -> None: ...

class PbCallEnd(_message.Message):
    __slots__ = ("call_id", "reason", "duration_sec")
    class EndReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        NORMAL: _ClassVar[PbCallEnd.EndReason]
        TIMEOUT: _ClassVar[PbCallEnd.EndReason]
        ERROR: _ClassVar[PbCallEnd.EndReason]
        REJECTED: _ClassVar[PbCallEnd.EndReason]
        BUSY: _ClassVar[PbCallEnd.EndReason]
    NORMAL: PbCallEnd.EndReason
    TIMEOUT: PbCallEnd.EndReason
    ERROR: PbCallEnd.EndReason
    REJECTED: PbCallEnd.EndReason
    BUSY: PbCallEnd.EndReason
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    DURATION_SEC_FIELD_NUMBER: _ClassVar[int]
    call_id: str
    reason: PbCallEnd.EndReason
    duration_sec: int
    def __init__(self, call_id: _Optional[str] = ..., reason: _Optional[_Union[PbCallEnd.EndReason, str]] = ..., duration_sec: _Optional[int] = ...) -> None: ...

class PbCallMediaControl(_message.Message):
    __slots__ = ("call_id", "audio_muted", "video_muted", "screen_sharing")
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    AUDIO_MUTED_FIELD_NUMBER: _ClassVar[int]
    VIDEO_MUTED_FIELD_NUMBER: _ClassVar[int]
    SCREEN_SHARING_FIELD_NUMBER: _ClassVar[int]
    call_id: str
    audio_muted: bool
    video_muted: bool
    screen_sharing: bool
    def __init__(self, call_id: _Optional[str] = ..., audio_muted: bool = ..., video_muted: bool = ..., screen_sharing: bool = ...) -> None: ...

class PbHubStream(_message.Message):
    __slots__ = ("action", "stream_id", "title", "broadcaster_nick", "media", "codecs", "viewer_count", "max_viewers", "bitrate", "description")
    class StreamAction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        START_STREAM: _ClassVar[PbHubStream.StreamAction]
        STOP_STREAM: _ClassVar[PbHubStream.StreamAction]
        STREAM_AVAILABLE: _ClassVar[PbHubStream.StreamAction]
        STREAM_ENDED: _ClassVar[PbHubStream.StreamAction]
        JOIN_STREAM: _ClassVar[PbHubStream.StreamAction]
        LEAVE_STREAM: _ClassVar[PbHubStream.StreamAction]
        STREAM_UPDATE: _ClassVar[PbHubStream.StreamAction]
    START_STREAM: PbHubStream.StreamAction
    STOP_STREAM: PbHubStream.StreamAction
    STREAM_AVAILABLE: PbHubStream.StreamAction
    STREAM_ENDED: PbHubStream.StreamAction
    JOIN_STREAM: PbHubStream.StreamAction
    LEAVE_STREAM: PbHubStream.StreamAction
    STREAM_UPDATE: PbHubStream.StreamAction
    ACTION_FIELD_NUMBER: _ClassVar[int]
    STREAM_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    BROADCASTER_NICK_FIELD_NUMBER: _ClassVar[int]
    MEDIA_FIELD_NUMBER: _ClassVar[int]
    CODECS_FIELD_NUMBER: _ClassVar[int]
    VIEWER_COUNT_FIELD_NUMBER: _ClassVar[int]
    MAX_VIEWERS_FIELD_NUMBER: _ClassVar[int]
    BITRATE_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    action: PbHubStream.StreamAction
    stream_id: str
    title: str
    broadcaster_nick: str
    media: _containers.RepeatedScalarFieldContainer[PbCallOffer.MediaType]
    codecs: _containers.RepeatedCompositeFieldContainer[CodecInfo]
    viewer_count: int
    max_viewers: int
    bitrate: int
    description: str
    def __init__(self, action: _Optional[_Union[PbHubStream.StreamAction, str]] = ..., stream_id: _Optional[str] = ..., title: _Optional[str] = ..., broadcaster_nick: _Optional[str] = ..., media: _Optional[_Iterable[_Union[PbCallOffer.MediaType, str]]] = ..., codecs: _Optional[_Iterable[_Union[CodecInfo, _Mapping]]] = ..., viewer_count: _Optional[int] = ..., max_viewers: _Optional[int] = ..., bitrate: _Optional[int] = ..., description: _Optional[str] = ...) -> None: ...
