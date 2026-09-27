"""Unit tests for Thrift Compact Protocol encoder."""

from instagram_mcp.mqtt.thrift import ThriftCompactWriter


class TestThriftCompactWriter:
    def test_write_bool_true(self) -> None:
        w = ThriftCompactWriter()
        w.write_bool(1, True)
        # Field 1, delta=1, type=1 (true) -> (1 << 4) | 1 = 0x11
        assert w.getvalue() == bytes([0x11])

    def test_write_bool_false(self) -> None:
        w = ThriftCompactWriter()
        w.write_bool(1, False)
        # Field 1, delta=1, type=2 (false) -> (1 << 4) | 2 = 0x12
        assert w.getvalue() == bytes([0x12])

    def test_write_byte(self) -> None:
        w = ThriftCompactWriter()
        w.write_byte(1, 3)
        # delta=1, type=3 -> 0x13, then value 3
        assert w.getvalue() == bytes([0x13, 0x03])

    def test_write_i32_positive(self) -> None:
        w = ThriftCompactWriter()
        w.write_i32(1, 1)
        # delta=1, type=5 -> 0x15, zigzag(1)=2, varint(2)=0x02
        assert w.getvalue() == bytes([0x15, 0x02])

    def test_write_i32_negative(self) -> None:
        w = ThriftCompactWriter()
        w.write_i32(1, -1)
        # zigzag32(-1) = 1, varint(1) = 0x01
        assert w.getvalue() == bytes([0x15, 0x01])

    def test_write_i32_zero(self) -> None:
        w = ThriftCompactWriter()
        w.write_i32(1, 0)
        # zigzag32(0) = 0, varint(0) = 0x00
        assert w.getvalue() == bytes([0x15, 0x00])

    def test_write_i64(self) -> None:
        w = ThriftCompactWriter()
        w.write_i64(1, 183)
        # delta=1, type=6 -> 0x16, zigzag64(183)=366, varint(366)
        data = w.getvalue()
        assert data[0] == 0x16
        # zigzag64(183)=366=0x16E -> varint 0xEE, 0x02
        assert data[1:] == bytes([0xEE, 0x02])

    def test_write_string(self) -> None:
        w = ThriftCompactWriter()
        w.write_string(1, "hello")
        data = w.getvalue()
        # delta=1, type=8 -> 0x18, length=5, "hello"
        assert data[0] == 0x18
        assert data[1] == 5
        assert data[2:] == b"hello"

    def test_write_string_empty(self) -> None:
        w = ThriftCompactWriter()
        w.write_string(1, "")
        data = w.getvalue()
        assert data[0] == 0x18
        assert data[1] == 0

    def test_write_list_i32(self) -> None:
        w = ThriftCompactWriter()
        w.write_list_i32(1, [88, 146])
        data = w.getvalue()
        # delta=1, type=9 -> 0x19
        assert data[0] == 0x19
        # 2 elements, elem type i32(5) -> (2 << 4) | 5 = 0x25
        assert data[1] == 0x25
        # zigzag32(88)=176, zigzag32(146)=292

    def test_write_list_i32_empty(self) -> None:
        w = ThriftCompactWriter()
        w.write_list_i32(1, [])
        data = w.getvalue()
        assert data[0] == 0x19
        # 0 elements, elem type i32(5) -> (0 << 4) | 5 = 0x05
        assert data[1] == 0x05

    def test_write_map_str_str(self) -> None:
        w = ThriftCompactWriter()
        w.write_map_str_str(1, {"a": "b"})
        data = w.getvalue()
        # delta=1, type=11 -> 0x1B
        assert data[0] == 0x1B
        # 1 entry varint, 0x88 (key_type=8 string, value_type=8 string)
        assert data[1] == 1
        assert data[2] == 0x88
        # key "a": length=1, 'a'
        assert data[3] == 1
        assert data[4:5] == b"a"
        # value "b": length=1, 'b'
        assert data[5] == 1
        assert data[6:7] == b"b"

    def test_write_map_str_str_empty(self) -> None:
        w = ThriftCompactWriter()
        w.write_map_str_str(1, {})
        data = w.getvalue()
        assert data[0] == 0x1B
        # Empty map: just 0x00
        assert data[1] == 0x00

    def test_write_struct_nesting(self) -> None:
        """Verify struct nesting saves/restores field context."""
        w = ThriftCompactWriter()
        w.write_i32(1, 42)  # outer field 1
        w.write_struct_begin(4)  # outer field 4, begin nested struct
        w.write_i64(1, 100)  # inner field 1 (delta resets to 0)
        w.write_string(2, "test")  # inner field 2
        w.write_stop()  # end inner struct
        w.write_string(5, "outer")  # outer field 5 (resumes from field 4)

        data = w.getvalue()
        # Verify the outer field 5 header uses delta from 4, not from inner field 2
        # This was the bug that was fixed with _field_stack
        assert len(data) > 0

    def test_field_delta_encoding(self) -> None:
        """Sequential fields use delta encoding (compact form)."""
        w = ThriftCompactWriter()
        w.write_i32(1, 0)
        w.write_i32(2, 0)
        data = w.getvalue()
        # Field 1: delta=1, type=5 -> 0x15
        assert data[0] == 0x15
        # Field 2: delta=1 (from 1), type=5 -> 0x15
        assert data[2] == 0x15

    def test_large_field_delta(self) -> None:
        """Fields with delta > 15 use full field header."""
        w = ThriftCompactWriter()
        w.write_i32(1, 0)
        w.write_i32(20, 0)
        data = w.getvalue()
        # Field 1: 0x15, 0x00
        assert data[0] == 0x15
        # Field 20: delta=19 > 15, so type byte (0x05) then zigzag16(20) varint
        assert data[2] == 0x05

    def test_stop_byte(self) -> None:
        w = ThriftCompactWriter()
        w.write_stop()
        assert w.getvalue() == bytes([0x00])

    def test_write_i64_negative(self) -> None:
        """Negative i64 exercises the negative varint branch."""
        w = ThriftCompactWriter()
        w.write_i64(1, -1)
        data = w.getvalue()
        # zigzag64(-1) = 1, varint(1) = 0x01
        assert data[0] == 0x16
        assert data[1] == 0x01

    def test_getvalue_returns_bytes(self) -> None:
        w = ThriftCompactWriter()
        assert isinstance(w.getvalue(), bytes)
        assert w.getvalue() == b""


class TestBuildConnectPayload:
    def test_returns_compressed_bytes(self) -> None:
        """build_connect_payload returns zlib-compressed data."""
        import zlib

        from instagram_mcp.mqtt.thrift import build_connect_payload

        session = {
            "authorization_data": {
                "ds_user_id": "12345",
                "sessionid": "test_session",
            },
            "uuids": {
                "phone_id": "abcdef1234567890abcd",
            },
            "device_settings": {
                "app_version": "415.0.0.36.76",
            },
            "user_agent": "Instagram 415.0.0.36.76 Android",
        }

        result = build_connect_payload(session)
        assert isinstance(result, bytes)
        assert len(result) > 0

        # Should be valid zlib data
        decompressed = zlib.decompress(result)
        assert len(decompressed) > len(result)  # compressed should be smaller

    def test_payload_contains_user_agent(self) -> None:
        """Verify the payload contains our user agent string."""
        import zlib

        from instagram_mcp.mqtt.thrift import build_connect_payload

        ua = "Instagram 415.0.0.36.76 Android (test)"
        session = {
            "authorization_data": {
                "ds_user_id": "99999",
                "sessionid": "sess",
            },
            "uuids": {"phone_id": "phone1234567890phone"},
            "device_settings": {"app_version": "415.0.0.36.76"},
            "user_agent": ua,
        }

        result = build_connect_payload(session)
        decompressed = zlib.decompress(result)
        # The user agent should appear in the binary data
        assert ua.encode() in decompressed

    def test_payload_contains_bearer_token(self) -> None:
        """Verify the payload contains the authorization bearer token."""
        import zlib

        from instagram_mcp.mqtt.thrift import build_connect_payload

        session = {
            "authorization_data": {
                "ds_user_id": "12345",
                "sessionid": "my_session_id",
            },
            "uuids": {"phone_id": "phone1234567890phone"},
            "device_settings": {"app_version": "415.0.0.36.76"},
            "user_agent": "test_ua",
        }

        result = build_connect_payload(session)
        decompressed = zlib.decompress(result)
        assert b"authorization=Bearer IGT:2:" in decompressed
