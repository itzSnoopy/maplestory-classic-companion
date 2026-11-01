from . import VERSION
from .config import Config
from .server import make_server


def main():
    cfg = Config.from_env()
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
