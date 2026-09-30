from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from onwatch.master_protocol import (
    MasterVersionError,
    decode_master_version_frame,
    decode_master_version_payload,
    discover_master_version,
)


CONTRACT = Path(__file__).parent / "fixtures" / "master_version_contract.json"
ENDPOINT = "https://versions.example.test/service/Version"


def test_shared_grpc_frame_contract():
    contract = json.loads(CONTRACT.read_text("utf-8"))
    for case in contract["cases"]:
        frame = bytes.fromhex(case["frameHex"])
        if case["valid"]:
            result = decode_master_version_frame(frame)
            assert result.version == case["masterVersion"]
            assert result.resource_version == case["resourceVersion"]
        else:
            with pytest.raises(ValueError):
                decode_master_version_frame(frame)


def test_payload_decoder_derives_resource_version_when_field_two_is_missing():
    frame = bytes.fromhex("00000000120a10312e302e302e3230322f6d6173746572")
    result = decode_master_version_frame(frame)
    assert result.resource_version == "1.0.0.202"


def test_invalid_protobuf_fails_closed_without_retry():
    request = Mock(return_value=b"\xff")
    with pytest.raises(MasterVersionError, match="invalid Master version response"):
        discover_master_version(ENDPOINT, request=request, sleep=Mock())
    request.assert_called_once()


def test_nonzero_grpc_status_fails_closed_after_retries():
    request = Mock(side_effect=MasterVersionError("grpc-status 14"))
    sleep = Mock()
    with pytest.raises(MasterVersionError, match="after 3 attempts"):
        discover_master_version(ENDPOINT, request=request, sleep=sleep)
    assert request.call_count == 3
    assert sleep.call_count == 2


def test_timeout_retries_then_fails_closed():
    request = Mock(side_effect=TimeoutError("deadline exceeded"))
    sleep = Mock()
    with pytest.raises(MasterVersionError, match="after 3 attempts"):
        discover_master_version(ENDPOINT, request=request, sleep=sleep)
    assert request.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [3.0, 8.0]


def test_version_endpoint_requires_https():
    with pytest.raises(ValueError, match="HTTPS"):
        discover_master_version("http://versions.example.test/service/Version")
