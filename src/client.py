"""VPN Client Entry Point.

Starts the VPN client with configured transport and TUN device.
"""

import argparse
import signal
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from common.config import load_config
from common.logger import setup_logger
from common.errors import VPNError
from tun.tun_device import create_tun_device
from transport.ssh_transport import SSHTransport
from core.client_core import ClientCore


logger = setup_logger(__name__)

# Global client instance for signal handling
_client: ClientCore | None = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down...")
    if _client:
        _client.stop()
    sys.exit(0)


def main():
    """Main client entry point."""
    parser = argparse.ArgumentParser(description="VPN Tunnel Client")
    parser.add_argument(
        "-c", "--config",
        default="config/client.yaml",
        help="Path to client configuration file",
    )
    parser.add_argument(
        "--mock-tun",
        action="store_true",
        help="Use mock TUN device instead of real TUN",
    )
    args = parser.parse_args()

    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("VPN Tunnel Client starting")

    try:
        # Load configuration
        config = load_config(args.config)
        logger.info(f"Configuration loaded from {args.config}")
        logger.info(f"Transport type: {config.transport.type}")

        # Create TUN device
        tun = create_tun_device(
            name=config.tun.name,
            mtu=config.tun.mtu,
            use_mock=args.mock_tun,
        )

        # Create transport
        if config.transport.type == "ssh":
            transport = SSHTransport(
                host=config.transport.host,
                port=config.transport.port,
                username=config.transport.username,
                key_file=config.transport.key_file,
                remote_host=config.server.host,
                remote_port=config.server.port,
            )
        else:
            raise VPNError(f"Unsupported transport type: {config.transport.type}")

        # Create and start client core
        global _client
        _client = ClientCore(
            tun=tun,
            transport=transport,
            session_id=1,
        )

        logger.info("Client core initialized, starting tunnel...")
        _client.start()

        logger.info("Tunnel established, running...")

        # Keep main thread alive
        while _client.is_connected():
            signal.pause()

    except VPNError as e:
        logger.error(f"Tunnel error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)
    finally:
        if _client:
            _client.stop()
        logger.info("VPN Tunnel Client stopped")


if __name__ == "__main__":
    main()
