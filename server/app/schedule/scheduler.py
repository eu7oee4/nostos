from app.config import settings


def start_scheduler() -> None:
    if not settings.proactive_enabled:
        return
    # TODO: APScheduler jobs for proactive reach-out
    raise NotImplementedError("proactive scheduler not implemented in scaffold")
