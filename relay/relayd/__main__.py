import sys

from . import VERSION
from .config import Config, ConfigError
from .server import make_server


def main():
    cfg = Config.from_env()
    try:
        cfg.validate()
    except ConfigError as e:
        print(f"relayd: {e}", file=sys.stderr, flush=True)
        sys.exit(2)
    srv = make_server(cfg, start_cleanup=True)
    print(f"relayd {VERSION} on {cfg.bind}:{srv.server_address[1]}, data in {cfg.data_dir}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.app.close()


if __name__ == "__main__":
    main()
