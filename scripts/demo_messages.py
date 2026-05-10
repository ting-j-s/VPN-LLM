"""Demo: server <-> client frame exchange — only TUN and core messages."""

import logging
import os
import sys
import threading
import time

# Import first — modules call get_logger() at import time at INFO level
from src.core.server_core import ServerCore
from src.core.client_core import ClientCore
from src.transport.base import MockTransport
from src.tun.tun_device import MockTunDevice

# Now override: raise transport noise, enable DEBUG for core + tun
logging.getLogger("src.transport.base").setLevel(logging.WARNING)

for name in ["src.core.server_core", "src.core.client_core", "src.tun.tun_device"]:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    # Replace existing handler format
    for h in logger.handlers:
        h.setLevel(logging.DEBUG)
        h.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(name)-28s] %(message)s",
            datefmt="%H:%M:%S",
        ))


print("=" * 60)
print("  Server <-> Client Frame Exchange")
print("=" * 60)

# ---- Setup ----
server_tun = MockTunDevice(name="server-tun", mtu=1400)
client_tun = MockTunDevice(name="client-tun", mtu=1400)
server_transport = MockTransport()
client_transport = MockTransport()

stop_wiring = threading.Event()

def wire_transports():
    while not stop_wiring.is_set():
        while client_transport._tx_data:
            server_transport.inject(client_transport._tx_data.pop(0))
        while server_transport._tx_data:
            client_transport.inject(server_transport._tx_data.pop(0))
        time.sleep(0.01)

wire_thread = threading.Thread(target=wire_transports, daemon=True)

server = ServerCore(tun=server_tun, transport=server_transport,
                    heartbeat_interval=2.0, heartbeat_timeout=30.0)
client = ClientCore(tun=client_tun, transport=client_transport,
                    heartbeat_interval=2.0, heartbeat_timeout=30.0)

# ---- Start ----
server_transport.connect()
client_transport.connect()

print("\n--- Starting SERVER ---")
server.start()
time.sleep(0.3)

wire_thread.start()

print("--- Starting CLIENT ---")
client.start()
time.sleep(0.5)

# ---- Client -> Server DATA ----
print("\n>>> CLIENT -> SERVER: injecting packet into client TUN")
client_tun.inject_packet(b"CLIENT_HELLO_PACKET")
time.sleep(0.5)

# ---- Server -> Client DATA ----
print(">>> SERVER -> CLIENT: injecting packet into server TUN")
server_tun.inject_packet(b"SERVER_HELLO_PACKET")
time.sleep(0.5)

# ---- Wait for heartbeat ----
print("\n--- Waiting for heartbeat exchange ---")
time.sleep(2.5)

# ---- Shutdown ----
print("\n--- Stopping CLIENT ---")
client.stop()
time.sleep(0.3)

print("--- Stopping SERVER ---")
server.stop()
time.sleep(0.3)

stop_wiring.set()

print()
print("=" * 60)
ss = server.get_stats()
cs = client.get_stats()
print(f"  Done. Server: ↑{ss['tun_to_transport_bytes']}B ↓{ss['transport_to_tun_bytes']}B  "
      f"Client: ↑{cs['tun_to_transport_bytes']}B ↓{cs['transport_to_tun_bytes']}B")
print("=" * 60)
