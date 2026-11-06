from .config import load_config
from .ui.app import MessyTeamApp


def main() -> None:
    app = MessyTeamApp(load_config())
    app.run()


if __name__ == "__main__":
    main()
