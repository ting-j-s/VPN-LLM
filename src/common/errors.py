"""VPN Tunnel Exception Definitions."""


class TunnelError(Exception):
    """Base exception for tunnel-related errors."""
    pass


class TransportError(TunnelError):
    """Transport layer errors."""
    pass


class ConnectionError(TunnelError):
    """Connection-related errors."""
    pass


class FrameError(TunnelError):
    """Frame encoding/decoding errors."""
    pass


class ConfigError(TunnelError):
    """Configuration errors."""
    pass


class TUNError(TunnelError):
    """TUN device errors."""
    pass


class ForwardingError(TunnelError):
    """Forwarding layer errors."""
    pass
