"""A cancelled update stays cancelled: nothing starts it again by itself until the user asks or a newer one appears."""
import time

from nebula.core.models import Game


def pump(app, until, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end and not until():
        app.processEvents()
        time.sleep(0.01)
    return until()


def test_cancelled_update_is_not_retried(qapp, services, monkeypatch):
    gog = services.integrations["gog"]
    g = Game("gog", "42", "Some Game", installed=True, install_path="/tmp", update_available=True)
    services.library.update_game(g)

    def slow(game, ctx, version=""):
        while True:
            ctx.check()
            time.sleep(0.01)

    monkeypatch.setattr(gog, "update", slow)
    monkeypatch.setattr(services.actions, "_newest", lambda game: "build-2")
    job = services.actions.update(g)
    assert pump(qapp, lambda: job.state == "running")
    services.jobs.cancel(job.id)
    assert job.stage == "Canceling…"
    assert pump(qapp, lambda: job.state == "canceled")
    assert pump(qapp, lambda: services.db.get_config(g.uid).get("update_declined") == "build-2")
    assert services.actions.declined(g)  # the automatic updater skips it

    monkeypatch.setattr(services.actions, "_newest", lambda game: "build-3")
    assert not services.actions.declined(g)  # a newer version is offered again

    monkeypatch.setattr(services.actions, "_newest", lambda game: "build-2")
    job2 = services.actions.update(g)  # the user asks again: automatic updates may resume
    assert not services.actions.declined(g)
    services.jobs.cancel(job2.id)
    pump(qapp, lambda: job2.state == "canceled")
