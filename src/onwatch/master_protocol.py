"""Official Master version service contract.

Protocol behavior is adapted from haneoka-gakuen/haneoka's
``scripts/ingest/master.py`` (MPL-2.0):
https://github.com/haneoka-gakuen/haneoka/blob/main/scripts/ingest/master.py
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


class MasterVersionError(RuntimeError):
    """The official Master version service could not provide a valid response."""


@dataclass(frozen=True)
class MasterVersion:
    version: str
    resource_version: str


_RESOURCE_VERSION = re.compile(r"^\d+(?:\.\d+){3}$")
_VERSION_PATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")


def _varint(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(data):
            raise ValueError("truncated Master version protobuf")
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, position
    raise ValueError("invalid Master version protobuf varint")


def decode_master_version_payload(data: bytes) -> MasterVersion:
    """Decode VersionResponse protobuf fields 1 and 2.

    Field 1 is ``masterVersion``. Field 2 is ``resourceVersion``; older service
    responses omit it, in which case the leading path segment of field 1 is the
    verified fallback contract.
    """
    fields: dict[int, str] = {}
    position = 0
    while position < len(data):
        tag, position = _varint(data, position)
        field, wire = tag >> 3, tag & 7
        if field == 0:
            raise ValueError("invalid Master version protobuf field number")
        if wire == 2:
            size, position = _varint(data, position)
            end = position + size
            if end > len(data):
                raise ValueError("truncated Master version protobuf field")
            if field in (1, 2):
                if field in fields:
                    raise ValueError(f"duplicate Master version protobuf field {field}")
                fields[field] = data[position:end].decode("utf-8")
            position = end
        elif wire == 0:
            _, position = _varint(data, position)
        elif wire in (1, 5):
            position += 8 if wire == 1 else 4
            if position > len(data):
                raise ValueError("truncated Master version protobuf scalar")
        else:
            raise ValueError(f"unsupported Master version protobuf wire type {wire}")

    version = fields.get(1, "")
    resource_version = fields.get(2) or version.partition("/")[0]
    if (
        not version
        or len(version) > 128
        or not _VERSION_PATH.fullmatch(version)
        or any(segment in {".", ".."} for segment in version.split("/"))
        or not resource_version
        or not _RESOURCE_VERSION.fullmatch(resource_version)
    ):
        raise ValueError("Master version response is incomplete or invalid")
    return MasterVersion(version=version, resource_version=resource_version)


def decode_master_version_frame(frame: bytes) -> MasterVersion:
    """Decode one uncompressed five-byte gRPC message frame for fixture tests."""
    if len(frame) < 5 or frame[0] != 0 or int.from_bytes(frame[1:5], "big") != len(frame) - 5:
        raise ValueError("invalid Master version gRPC frame")
    return decode_master_version_payload(frame[5:])


def _validated_endpoint(endpoint: str):
    parsed = urlsplit(endpoint.strip())
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Master version endpoint has an invalid port") from error
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        raise ValueError("Master version endpoint must be a plain HTTPS gRPC URL")
    return parsed


def _grpc_request(endpoint: str, timeout: float) -> bytes:
    try:
        import grpc
    except ImportError as error:  # pragma: no cover - exercised in packaged CI
        raise RuntimeError("grpcio is required for official Master version discovery") from error

    parsed = _validated_endpoint(endpoint)
    channel = grpc.secure_channel(parsed.netloc, grpc.ssl_channel_credentials())
    try:
        call = channel.unary_unary(
            parsed.path,
            request_serializer=lambda value: value,
            response_deserializer=lambda value: value,
        )
        payload, result = call.with_call(
            b"",
            timeout=timeout,
            metadata=(("x-platform", "Android"),),
        )
        if result.code() != grpc.StatusCode.OK:
            raise MasterVersionError(f"Master version gRPC status is {result.code().name}")
        return payload
    except grpc.RpcError as error:
        code = error.code()
        raise MasterVersionError(f"Master version gRPC request failed: {getattr(code, 'name', code)}") from error
    finally:
        channel.close()


def discover_master_version(
    endpoint: str,
    *,
    timeout: float = 30.0,
    retries: int = 3,
    retry_delays: tuple[float, ...] = (0.0, 3.0, 8.0),
    request=None,
    sleep=time.sleep,
) -> MasterVersion:
    """Call the official HTTP/2 gRPC Version method and fail closed."""
    _validated_endpoint(endpoint)
    if retries < 1:
        raise ValueError("Master version retries must be positive")
    invoke = request or _grpc_request
    last_error: Exception | None = None
    for attempt in range(retries):
        delay = retry_delays[attempt] if attempt < len(retry_delays) else 0.0
        if delay:
            sleep(delay)
        try:
            return decode_master_version_payload(invoke(endpoint, timeout))
        except MasterVersionError as error:
            last_error = error
        except (OSError, TimeoutError, ValueError) as error:
            # Invalid protobuf is not recoverable; connection and timeout errors are.
            if isinstance(error, ValueError):
                raise MasterVersionError(f"invalid Master version response: {error}") from error
            last_error = error
        if attempt + 1 >= retries:
            break
    raise MasterVersionError(f"Master version request failed after {retries} attempts: {last_error}") from last_error
