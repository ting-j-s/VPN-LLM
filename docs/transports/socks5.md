# SOCKS5 Transport (Skeleton)

## Status

**Skeleton – not yet implemented.**  
This transport exists as a placeholder for future SOCKS5 support.  
The class is importable and constructable, but all network methods raise `NotImplementedError`.

## Description

The SOCKS5 transport will provide SSH‑ or TLS‑like encapsulation over a SOCKS5 proxy.  
It will implement the `Transport` interface (connect, send, recv, close) using standard SOCKS5 protocols.

## Usage (once implemented)

```yaml
# Example server configuration snippet
transport:
  type: socks5
  # Future options may include proxy address, authentication, etc.
```

```yaml
# Example client configuration snippet
transport:
  type: socks5
```

## Implementation Roadmap

1. Implement SOCKS5 handshake and connection establishment.
2. Add configuration options (address, port, authentication).
3. Integrate with the transport factory and VPN core.
4. Write full unit and integration tests.

## References

- [SOCKS5 RFC 1928](https://tools.ietf.org/html/rfc1928)
