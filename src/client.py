"""VPN Client Entry Point.

Starts the VPN client with configured transport and TUN device.
"""

import argparse
import signal
import sys
from pathlib import Path

from .common.config import load_client_config
from .common.logger import get_logger
from .common.errors import VPNError
from .tun.tun_device import create_tun_device
from .transport.factory import create_transport
from .core.client_core import ClientCore


logger = get_logger(__name__)

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
    logger.info("VPN Tunnel Client starting")
    logger.info("=" * 50)

    try:
        # Load configuration
        config = load_client_config(args.config)
        logger.info(f"Configuration loaded: {args.config}")
        logger.info(f"Transport type: {config.transport.type}")

        # Override transport type if --transport specified
        if args.transport:
            config.transport.type = args.transport
            logger.info(f"Transport type overridden to: {args.transport}")

        # Create TUN device
        if args.mock_tun:
            tun = create_tun_device(
                name=config.client.tun_name,
                mtu=config.client.mtu,
                use_mock=True,
            )
            logger.info(f"Using MockTunDevice (name={config.client.tun_name}, mtu={config.client.mtu})")
        else:
            tun = create_tun_device(
                name=config.client.tun_name,
                mtu=config.client.mtu,
                use_mock=False,
            )
            logger.info(f"Using LinuxTunDevice (name={config.client.tun_name}, mtu={config.client.mtu})")

        # Create transport using factory
        transport = create_transport(config)
        logger.info(f"Transport created: {transport}")

        # Create client core
        global _client
        _client = ClientCore(
            tun=tun,
            transport=transport,
            heartbeat_interval=config.session.heartbeat_interval,
            heartbeat_timeout=config.session.heartbeat_timeout,
        )
        logger.info("ClientCore created")

        # Start client
        logger.info("Starting tunnel...")
        _client.start()

        logger.info("=" * 50)
        logger.info("Tunnel established, running...")
        logger.info("Press Ctrl+C to stop")
        logger.info("=" * 50)

        # Keep main thread alive
        while _client and _client.is_connected():
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
        if _client:
            _client.stop()
        logger.info("VPN Tunnel Client stopped")


if __name__ == "__main__":
    main()
