"""VPN Tunnel Exception Definitions."""


class VPNError(Exception):
    """Base exception for all VPN tunnel errors."""
    pass


class ConfigError(VPNError):
    """Configuration errors."""
    pass


class FrameDecodeError(VPNError):
    """Frame decoding/encoding errors."""
    pass


class TransportError(VPNError):
    """Transport layer errors."""
    pass


class TransportTimeout(TransportError):
    """Transport recv timeout - not an error, just no data available."""
    pass


class TunDeviceError(VPNError):
    """TUN device errors."""
    pass


class ForwardingError(VPNError):
    """Forwarding layer errors."""
    pass