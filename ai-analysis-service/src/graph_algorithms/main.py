from __future__ import annotations

import logging

from .service import GraphAlgorithmsService


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    GraphAlgorithmsService().run()


if __name__ == "__main__":
    main()
