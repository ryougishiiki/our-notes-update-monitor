from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from onwatch.master_protocol import (
    MasterVersionError,
    decode_master_version_frame,
    discover_master_version,
)


CONTRACT = Path(__file__).parent / "fixtures" / "master_version_contract.json"
ENDPOINT = "https://versions.example.test/service/Version"


class MasterVersionProtocolTests(unittest.TestCase):
    def test_shared_grpc_frame_contract(self) -> None:
        contract = json.loads(CONTRACT.read_text("utf-8"))
        for case in contract["cases"]:
            with self.subTest(case=case["name"]):
                frame = bytes.fromhex(case["frameHex"])
                if case["valid"]:
                    result = decode_master_version_frame(frame)
                    self.assertEqual(result.version, case["masterVersion"])
                    self.assertEqual(result.resource_version, case["resourceVersion"])
                else:
                    with self.assertRaises(ValueError):
                        decode_master_version_frame(frame)

    def test_payload_decoder_derives_resource_version_when_field_two_is_missing(self) -> None:
        frame = bytes.fromhex("00000000120a10312e302e302e3230322f6d6173746572")
        result = decode_master_version_frame(frame)
        self.assertEqual(result.resource_version, "1.0.0.202")

    def test_invalid_protobuf_fails_closed_without_retry(self) -> None:
        request = Mock(return_value=b"\xff")
        with self.assertRaisesRegex(MasterVersionError, "invalid Master version response"):
            discover_master_version(ENDPOINT, request=request, sleep=Mock())
        request.assert_called_once()

    def test_nonzero_grpc_status_fails_closed_after_retries(self) -> None:
        request = Mock(side_effect=MasterVersionError("grpc-status 14"))
        sleep = Mock()
        with self.assertRaisesRegex(MasterVersionError, "after 3 attempts"):
            discover_master_version(ENDPOINT, request=request, sleep=sleep)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_timeout_retries_then_fails_closed(self) -> None:
        request = Mock(side_effect=TimeoutError("deadline exceeded"))
        sleep = Mock()
        with self.assertRaisesRegex(MasterVersionError, "after 3 attempts"):
            discover_master_version(ENDPOINT, request=request, sleep=sleep)
        self.assertEqual(request.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [3.0, 8.0])

    def test_version_endpoint_requires_https(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            discover_master_version("http://versions.example.test/service/Version")
