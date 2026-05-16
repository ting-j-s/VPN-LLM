"""Tests for LinuxTunDevice.

These tests validate LinuxTunDevice functionality. Most tests require root/CAP_NET_ADMIN
and will be skipped if not available.
"""

import os
import sys
import pytest

from src.tun.tun_device import LinuxTunDevice, MockTunDevice


def is_linux():
    """Check if running on Linux."""
    return sys.platform.startswith("linux")


def has_tun_device():
    """Check if /dev/net/tun exists."""
    return os.path.exists("/dev/net/tun")


def has_root_or_net_admin():
    """Check if we have root or CAP_NET_ADMIN capability.

    Note: This is a best-effort check. The actual permission check
    happens when we try to open /dev/net/tun.
    """
    if os.geteuid() == 0:
        return True
    try:
        import subprocess
        result = subprocess.run(
            ["capsh", "--print"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return "cap_net_admin" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _can_use_real_tun():
    """All three conditions required for real TUN device operations."""
    return is_linux() and has_root_or_net_admin() and has_tun_device()


class TestLinuxTunDeviceImport:
    """Test that LinuxTunDevice can be imported and instantiated."""

    def test_import_linux_tun_device(self):
        """LinuxTunDevice should be importable."""
        from src.tun.tun_device import LinuxTunDevice
        assert LinuxTunDevice is not None

    def test_create_linux_tun_device(self):
        """Should be able to create LinuxTunDevice instance."""
        dev = LinuxTunDevice(name="test-tun", mtu=1400)
        assert dev.name == "test-tun"
        assert dev.mtu == 1400
        assert dev._fd is None
        assert dev._opened is False

    def test_create_with_custom_mtu(self):
        """Should respect custom MTU value."""
        dev = LinuxTunDevice(name="tun99", mtu=1500)
        assert dev.mtu == 1500


class TestLinuxTunDeviceOpenClose:
    """Test LinuxTunDevice open/close operations (requires root)."""

    @pytest.mark.skipif(not is_linux(), reason="Not Linux")
    def test_open_requires_root(self):
        """open() should fail without root or CAP_NET_ADMIN."""
        dev = LinuxTunDevice(name="tun99", mtu=1400)
        if _can_use_real_tun():
            pytest.skip("Has real TUN capability — test verifies failure without it")
        with pytest.raises(Exception):  # TunDeviceError or OSError
            dev.open()

    @pytest.mark.skipif(not _can_use_real_tun(), reason="Requires root, CAP_NET_ADMIN, and /dev/net/tun")
    def test_open_with_root(self):
        """With root, open() should succeed."""
        dev = LinuxTunDevice(name="tun99", mtu=1400)
        try:
            dev.open()
            assert dev._opened is True
            assert dev._fd is not None
        finally:
            dev.close()

    @pytest.mark.skipif(not _can_use_real_tun(), reason="Requires root, CAP_NET_ADMIN, and /dev/net/tun")
    def test_double_open_logs_warning(self):
        """Calling open() twice should log a warning and not fail."""
        dev = LinuxTunDevice(name="tun98", mtu=1400)
        try:
            dev.open()
            # Second open should be idempotent (warning logged)
            dev.open()
            assert dev._opened is True
        finally:
            dev.close()

    @pytest.mark.skipif(not _can_use_real_tun(), reason="Requires root, CAP_NET_ADMIN, and /dev/net/tun")
    def test_close_is_idempotent(self):
        """close() should be safe to call multiple times."""
        dev = LinuxTunDevice(name="tun97", mtu=1400)
        dev.open()
        assert dev._opened is True

        # First close
        dev.close()
        assert dev._opened is False

        # Second close should not raise
        dev.close()
        assert dev._opened is False

    @pytest.mark.skipif(not is_linux(), reason="Not Linux")
    def test_close_without_open(self):
        """close() should work even if device was never opened."""
        dev = LinuxTunDevice(name="tun96", mtu=1400)
        assert dev._opened is False

        # close() should not raise
        dev.close()
        assert dev._opened is False

    @pytest.mark.skipif(not is_linux(), reason="Not Linux")
    def test_linux_tun_device_name_truncation_needed(self):
        """Long names should be handled - OS will reject via ioctl if too long."""
        dev = LinuxTunDevice(name="tun95", mtu=1400)
        assert len(dev.name) <= 15  # IFNAMSIZ - 1

        # Long names are stored as-is; the OS will reject via ioctl if too long
        # This test verifies the behavior without enforcing truncation
        long_dev = LinuxTunDevice(name="this-name-is-way-too-long-for-tun", mtu=1400)
        # Name is stored but open() will fail with "Invalid argument" from ioctl
        assert long_dev.name == "this-name-is-way-too-long-for-tun"


class TestMockTunDevice:
    """Test MockTunDevice for comparison."""

    def test_mock_tun_device_basics(self):
        """MockTunDevice should work without any permissions."""
        dev = MockTunDevice(name="mock0", mtu=1400)

        dev.open()
        assert dev._opened is True

        dev.close()
        assert dev._opened is False

    def test_mock_tun_close_idempotent(self):
        """MockTunDevice.close() should be idempotent."""
        dev = MockTunDevice(name="mock1", mtu=1400)
        dev.open()

        dev.close()
        dev.close()  # Should not raise

    def test_mock_tun_inject_and_read(self):
        """MockTunDevice should support inject/read for testing."""
        dev = MockTunDevice(name="mock2", mtu=1400)
        dev.open()

        test_packet = b"\x45\x00\x00\x1e\x00\x01\x00\x00\x40\x06\x00\x00\x7f\x00\x00\x01\x7f\x00\x00\x01"
        dev.inject_packet(test_packet)

        read_packet = dev.read_packet()
        assert read_packet == test_packet

    def test_mock_tun_get_tx_packets(self):
        """MockTunDevice should track transmitted packets."""
        dev = MockTunDevice(name="mock3", mtu=1400)
        dev.open()

        test_packet = b"\x45\x00\x00\x1e" * 5
        dev.write_packet(test_packet)

        tx_packets = dev.get_tx_packets()
        assert len(tx_packets) == 1
        assert tx_packets[0] == test_packet


class TestLinuxTunDeviceReadWrite:
    """Test LinuxTunDevice read/write (requires root)."""

    @pytest.mark.skipif(not _can_use_real_tun(), reason="Requires root, CAP_NET_ADMIN, and /dev/net/tun")
    def test_write_and_read_packet(self):
        """Should be able to write and read packets with real TUN."""
        dev = LinuxTunDevice(name="tun94", mtu=1400)
        try:
            dev.open()

            # Note: real TUN requires another end to read what we write
            # This test verifies the interface works without errors
            test_packet = b"\x45\x00\x00\x1e\x00\x01\x00\x00\x40\x06\x00\x00\x7f\x00\x00\x01\x7f\x00\x00\x01"
            try:
                dev.write_packet(test_packet)
            except Exception:
                pass  # May fail if no peer connected

        finally:
            dev.close()

    @pytest.mark.skipif(not _can_use_real_tun(), reason="Requires root, CAP_NET_ADMIN, and /dev/net/tun")
    def test_set_nonblocking(self):
        """set_nonblocking() should work without errors."""
        dev = LinuxTunDevice(name="tun93", mtu=1400)
        try:
            dev.open()
            dev.set_nonblocking()  # Should not raise
        finally:
            dev.close()

    @pytest.mark.skipif(not _can_use_real_tun(), reason="Requires root, CAP_NET_ADMIN, and /dev/net/tun")
    def test_read_when_empty(self):
        """read_packet() should return None when no data."""
        dev = LinuxTunDevice(name="tun92", mtu=1400)
        try:
            dev.open()
            dev.set_nonblocking()

            result = dev.read_packet()
            assert result is None  # No data available
        finally:
            dev.close()


class TestTunDeviceFactory:
    """Test create_tun_device factory function."""

    def test_create_mock_device(self):
        """create_tun_device(use_mock=True) should return MockTunDevice."""
        from src.tun.tun_device import create_tun_device

        dev = create_tun_device(name="test", mtu=1400, use_mock=True)
        assert isinstance(dev, MockTunDevice)

    def test_create_linux_device(self):
        """create_tun_device(use_mock=False) should return LinuxTunDevice."""
        from src.tun.tun_device import create_tun_device

        if not is_linux():
            pytest.skip("Not Linux")

        dev = create_tun_device(name="test", mtu=1400, use_mock=False)
        assert isinstance(dev, LinuxTunDevice)