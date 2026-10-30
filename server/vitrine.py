"""Point d'entrée : lance le serveur web (python -m server.vitrine)."""
import uvicorn

from .config import CFG


def main():
    uvicorn.run("server.app:app", host=CFG.host, port=CFG.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
