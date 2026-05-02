"""VPN Server Entry Point.

Starts the VPN server with configured transport and TUN device.
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
from core.server_core import ServerCore


logger = setup_logger(__name__)

# Global server instance for signal handling
_server: ServerCore | None = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down...")
    if _server:
        _server.stop()
    sys.exit(0)


def main():
    """Main server entry point."""
    parser = argparse.ArgumentParser(description="VPN Tunnel Server")
    parser.add_argument(
        "-c", "--config",
        default="config/server.yaml",
        help="Path to server configuration file",
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

    logger.info("VPN Tunnel Server starting")

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

        # Create transport (SSH server transport)
        if config.transport.type == "ssh":
            transport = SSHTransport(
                host=config.transport.host,
                port=config.transport.port,
                key_file=config.transport.key_file,
            )
        else:
            raise VPNError(f"Unsupported transport type: {config.transport.type}")

        # Create and start server core
        global _server
        _server = ServerCore(
            tun=tun,
            transport=transport,
            session_id=1,
        )

        logger.info("Server core initialized, starting tunnel...")
        _server.start()

        logger.info("Tunnel listening, running...")

        # Keep main thread alive
        while _server.is_connected() or True:
            signal.pause()

    except VPNError as e:
        logger.error(f"Tunnel error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)
    finally:
        if _server:
            _server.stop()
        logger.info("VPN Tunnel Server stopped")


if __name__ == "__main__":
    main()
