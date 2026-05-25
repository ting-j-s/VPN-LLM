# SOCKS5 Transport

## Status

**Runtime implementation.**  
The transport supports client and server modes with full SOCKS5 handshake
(RFC 1928 / 1929) and length‑prefixed data framing matching TCP/TLS
transports.

## Description

The SOCKS5 transport establishes a tunnel through a SOCKS5 proxy (client)
or acts as a SOCKS5 server (server). After the SOCKS5 CONNECT handshake,
the raw socket is used for the VPN tunnel with a 4‑byte length prefix.

### Client mode

1. Connects to a SOCKS5 proxy.
2. Performs authentication (no‑auth or username/password).
3. Sends a CONNECT request to the target VPN server.
4. Uses the established stream for send/recv.

### Server mode

1. Binds a listening socket.
2. Accepts a connection and performs the SOCKS5 server handshake.
3. Accepts any CONNECT request (ignores the target address).
4. Uses the socket for send/recv.

## Configuration

All SOCKS5‑specific options are inside the `transport` section.

| Field                 | Default       | Description                                      |
|-----------------------|---------------|--------------------------------------------------|
| `type`                | `"socks5"`    | Must be `"socks5"`.                              |
| `socks5_proxy_host`   | `"127.0.0.1"` | Proxy address (client only).                     |
| `socks5_proxy_port`   | `1080`        | Proxy port (client only).                        |
| `socks5_username`     | `None`        | Optional username for authentication.            |
| `socks5_password`     | `None`        | Optional password for authentication.            |

The target host/port for the client are taken from the `server` section
(`server.host` / `server.port`).  
The server listen port is taken from `server.listen_port`.

## Example

**Client:**
```yaml
server:
  host: vpn.example.com
  port: 2226
transport:
  type: socks5
  socks5_proxy_host: 10.0.0.1
  socks5_proxy_port: 1080
  socks5_username: user
  socks5_password: secret
```

**Server:**
```yaml
server:
  listen_port: 2226
transport:
  type: socks5
  socks5_username: user
  socks5_password: secret
```

## Testing

```bash
pytest tests/test_socks5_transport.py -v
```

## Limitations

- Only IPv4 and domain‑name targets are supported (no IPv6).
- UDP ASSOCIATE and BIND commands are not implemented.
- The server accepts any CONNECT address; it does not forward traffic.

## References

- [SOCKS5 RFC 1928](https://tools.ietf.org/html/rfc1928)
- [SOCKS5 Username/Password Auth RFC 1929](https://tools.ietf.org/html/rfc1929)
