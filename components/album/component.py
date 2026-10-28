"""album component: the monster album follows the game. Seen mobs (map_enter), first kills (exp_rise),
who plays and their level (facts), the album.html page (rebuilt a moment after a change).

Subscribes: service_start, observation, map_enter, facts, exp_rise, service_stop (nothing to do).
Emits: first_kill (the diary component writes the beat; the journal line is written here).
Asks: hub.ask("seen_places") (diary) and hub.ask("visit_map") (skillbar's training feed).
Note (Phase 4): album.py still imports the diary for its html page / cli.
"""
import time
import traceback
from datetime import datetime

from base import perf
from base.contract import Component
from components.album import album


class AlbumComponent(Component):
    name = "album"

    def attach(self, hub):
        self.hub = hub
        album.set_root(hub.root)
        self.album = None               # album.Album while the service runs
        self.render_at = 0.0            # album.html after a first kill's pictures exist
        for event, fn in (("service_start", self.on_start), ("session_start", self.on_session_start),
                          ("observation", self.on_observation), ("map_enter", self.on_enter),
                          ("facts", self.on_facts), ("exp_rise", self.on_rise)):
            hub.on(event, fn)

    def on_start(self, **_):
        self.album = album.Album(self.hub.root)          # first kills, seen mobs

    def on_session_start(self, **_):
        self.render_at = 0.0

    def on_observation(self, **_):
        if self.render_at and time.time() >= self.render_at and self.album is not None:
            self.render_at = 0.0
            album.render(self.album)

    def on_enter(self, map, **_):
        try:
            if self.album.on_map(map):            # new mobs seen: grey in the album
                self.render_at = self.render_at or time.time()
        except Exception:
            self.hub.log("album: " + traceback.format_exc())

    def on_facts(self, prof, **_):
        """Who plays, their level, and whether the middle square is worth keeping (screen.py)."""
        hub, alb = self.hub, self.album
        try:
            with perf.timed("album"):
                if prof.who:
                    alb.set_who(prof.who, (prof.char() or {}).get("name"))
                    if not alb.char().get("seeded"):
                        alb.seed(hub.ask("seen_places", ()))
                if hub.state.in_session:
                    if alb.set_level(hub.state.level):
                        self.render_at = self.render_at or time.time()
                    hub.tell_source(album_watch=alb.wants_photo(hub.ask("visit_map")))
                else:
                    hub.tell_source(album_watch=False)
        except Exception:
            hub.log("album: " + traceback.format_exc())

    def on_rise(self, t, map, d, party, gap=None, **_):
        """Every EXP rise: a first kill for the monster album?"""
        try:
            if not self.hub.state.in_session:
                return
            mob = self.album.first_kill(map, d, party, gap)
            if mob is not None:
                self.first_kill(mob, map, t)
        except Exception:
            self.hub.log("album: " + traceback.format_exc())

    def first_kill(self, mob, map_name, t=None):
        """The first kill of this mob: photo (the middle square from just before, then a full
        screenshot + close-up), album record, diary beat (event), journal line."""
        hub, alb, st = self.hub, self.album, self.hub.state
        stem = f"{datetime.now():%Y-%m-%d_%H%M%S}_firstkill"
        shots = hub.album_shot(hub.shots_dir, stem, t)
        alb.record(mob, map_name, st.level, shots)
        hub.emit("first_kill", mob=mob, map=map_name, level=st.level, job=st.job, shots=shots,
                 caught=alb.count(), text=album.first_kill_text(mob, map_name))
        hub.journal(f"First {'boss kill' if mob.get('boss') else 'kill'}: {mob['name']}"
                    + (f" @ {map_name}" if map_name else "") + f" (album: {alb.count()})")
        hub.log(f"album: first kill {mob['name']} at {map_name} ({alb.count()} caught)")
        self.render_at = time.time() + 3
