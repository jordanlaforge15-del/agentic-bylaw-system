"""Entry point: python -m scripts.night_manager"""

import asyncio
import sys

from .config import parse_args
from .main import run


def cli() -> None:
    config = parse_args()
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        print("\n[NM] Interrupted by user. Exiting.")
        sys.exit(130)


cli()
