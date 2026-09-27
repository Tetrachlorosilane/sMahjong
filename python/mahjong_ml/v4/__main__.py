"""`python -m mahjong_ml.v4 …` 的入口（转 `cli.main`）。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
