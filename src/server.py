"""VPN Server Entry Point.

Starts the VPN server with configured transport and TUN device.
"""

import argparse
import signal
import sys
from pathlib import Path

from .common.config import load_server_config
from .common.logger import get_logger
from .common.errors import VPNError
from .tun.tun_device import create_tun_device
from .transport.factory import create_transport
from .core.server_core import ServerCore


logger = get_logger(__name__)

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
        help="Use MockTunDevice instead of LinuxTunDevice",
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=["ssh", "tcp", "tls", "websocket", "mock"],
        help="Override transport type from config",
    )
    args = parser.parse_args()

    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("=" * 50)
    logger.info("VPN Tunnel Server starting")
    logger.info("=" * 50)

    try:
        # Load configuration
        config = load_server_config(args.config)
        logger.info(f"Configuration loaded: {args.config}")
        logger.info(f"Transport type: {config.transport.type}")

        # Override transport type if --transport specified
        if args.transport:
            config.transport.type = args.transport
            logger.info(f"Transport type overridden to: {args.transport}")

        # Create TUN device
        if args.mock_tun:
            tun = create_tun_device(
                name=config.server.tun_name,
                mtu=config.server.mtu,
                use_mock=True,
            )
            logger.info(f"Using MockTunDevice (name={config.server.tun_name}, mtu={config.server.mtu})")
        else:
            tun = create_tun_device(
                name=config.server.tun_name,
                mtu=config.server.mtu,
                use_mock=False,
            )
            logger.info(f"Using LinuxTunDevice (name={config.server.tun_name}, mtu={config.server.mtu})")

        # Create transport using factory
        transport = create_transport(config)
        logger.info(f"Transport created: {transport}")

        # Create server core
        global _server
        _server = ServerCore(
            tun=tun,
            transport=transport,
            heartbeat_interval=config.session.heartbeat_interval,
            heartbeat_timeout=config.session.heartbeat_timeout,
        )
        logger.info("ServerCore created")

        # Start server
        logger.info("Starting tunnel...")
        _server.start()

        logger.info("=" * 50)
        logger.info("Tunnel listening, running...")
        logger.info("Press Ctrl+C to stop")
        logger.info("=" * 50)

        # Keep main thread alive
        while _server.is_connected() or True:
            signal.pause()

    except VPNError as e:
        logger.error(f"Tunnel error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)
    finally:
        if _server:
            _server.stop()
        logger.info("VPN Tunnel Server stopped")


if __name__ == "__main__":
    main()
