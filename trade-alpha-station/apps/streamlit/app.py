"""Runnable Streamlit entry point; production deployments inject a database backend."""

from trade_alpha.ui import InMemoryBackend, Role, UserContext, render_streamlit


def main() -> None:
    # The empty fixture makes the entry point safe to start locally.  Scores are
    # never calculated in this layer; real deployments replace this backend.
    render_streamlit(InMemoryBackend((), ()), UserContext(user_id="local-viewer", role=Role.VIEWER))


if __name__ == "__main__":
    main()
