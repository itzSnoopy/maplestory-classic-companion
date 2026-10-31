"""diary component: the tracker's events become diary beats (diary.py), plus the helper's camera
button and notes (requests the helper leaves in diary_requests.jsonl).

Subscribes: service_start (places seen before the diary), loop (requests), poll (wake the wait loop
when one is waiting), observation (delayed re-render once late pictures exist), levelup, death, job_change,
map_enter, moment, first_kill (from the album), session_end (render).
Offers: hub.ask("seen_places").
Helper side (ui): "Open diary" in the menu, the camera button, the pop-up card on new entries, Settings >
Diary, and the diary server (it hosts the pages other components register, hub/pages.py).
"""
import time
import traceback
from datetime import datetime

from base.contract import Component
from components.diary import diary
from hub import helper_settings as HS


class PageHost:
    """The diary server as hub/pages.py's host; the server module is imported when first used."""
    def open_page(self, path, in_process=False):
        from components.diary import diary_server
        return diary_server.open_page(path, in_process=in_process)

    def serve(self, **kw):
        from components.diary import diary_server
        return diary_server.serve(**kw)


class DiaryComponent(Component):
    name = "diary"
    settings_defaults = {"diary": HS.DEFAULTS["diary"]}     # the store keeps the slices in one file

    def attach(self, hub):
        self.hub = hub
        diary.set_root(hub.root)
        self.render_at = 0.0            # re-render the diary when late pictures (+2 s, +10 s) exist
        hub.provide("seen_places", diary.seen_places)
        for event, fn in (("service_start", self.on_start), ("loop", self.on_loop),
                          ("poll", self.on_poll), ("observation", self.on_observation),
                          ("levelup", self.on_levelup), ("death", self.on_death),
                          ("job_change", self.on_job), ("map_enter", self.on_enter),
                          ("moment", self.on_moment), ("first_kill", self.on_first_kill),
                          ("session_end", self.on_session_end)):
            hub.on(event, fn)

    # -- the helper window
    def ui(self, shell):
        from hub import pages
        self.shell = shell
        self.popup = None                 # diary_popup.DiaryPopup: a card on new diary entries
        if not shell.demo and not shell.headless:
            try:
                from components.diary.diary_popup import DiaryPopup
                self.popup = DiaryPopup(shell)
            except Exception as e:
                print(f"diary pop-up unavailable: {e}")
        pages.set_host(PageHost())        # the diary server opens and serves the registered pages
        shell.set_camera(diary.request_moment)
        shell.add_command("diary", self.open)
        shell.add_menu([("Open diary", "diary", None, None, None)], slot="open")
        shell.add_settings_page("Diary", self.page, after="Discord")
        shell.on_refresh(self.check_popup)

    def open(self):
        try:
            diary.open_diary(in_process=True)
        except Exception as e:
            print(f"open diary failed: {e}")

    def page(self, win, frame):
        from components.diary import diary_popup
        return diary_popup.page(win, frame, self.popup)

    def check_popup(self, snap):
        if getattr(self.shell, "panels_hidden", False):
            return                        # minimized: pop-ups wait until the window is open again
        if self.popup:
            try:
                self.popup.check()
            except Exception as e:
                print(f"diary pop-up check failed: {e}")

    def _shots(self, kind, when, delays=(0,)):
        """Ask the source for diary pictures; paths relative to the data folder."""
        stem = f"{when:%Y-%m-%d_%H%M%S}_{kind}"
        if max(delays) > 0:     # re-render the diary once the delayed pictures exist
            self.render_at = max(self.render_at, time.time() + max(delays) + 1)
        return self.hub.snapshot(diary.SHOTS, stem, delays)

    # -- service
    def on_start(self, maps, **_):
        diary.seed_places(maps)         # maps visited before the diary existed: not first visits

    def on_session_end(self, **_):
        diary.render()

    def on_observation(self, **_):
        if self.render_at and time.time() >= self.render_at:
            self.render_at = 0.0
            diary.render()

    # -- the helper's requests: `python -m components.diary.diary note "..."`, the camera button
    def on_poll(self, **_):
        if diary.has_requests():                 # answer now
            self.hub.wake()

    def on_loop(self, **_):
        hub, st = self.hub, self.hub.state
        for req in diary.take_requests():
            try:
                if req.get("kind") == "moment":
                    if st.in_session and st.playing:
                        self.snap()
                        hub.log("diary moment captured (camera button)")
                    else:
                        hub.log("camera button: no game on screen, nothing captured")
                    continue
                if st.in_session:
                    self.note(req["text"])
                else:
                    diary.add("note", req["text"])
                hub.log(f"diary note added: {req['text']}")
            except Exception:
                hub.log("diary note failed: " + traceback.format_exc())

    def snap(self):
        """Camera button: a moment entry with a fresh frame (the last reading's frame can be 30 s
        old; a 0.5 s delay makes the next quick look grab a new one)."""
        st = self.hub.state
        diary.add("moment", diary.SNAP_TEXT, level=st.level, map=st.map, job=st.job,
                  shots=self._shots("moment", datetime.now(), delays=(0.5,)), manual=True)

    def note(self, text):
        st = self.hub.state
        diary.add("note", text, level=st.level, map=st.map, job=st.job,
                  shots=self._shots("note", datetime.now()))

    # -- game events
    def _guard(self, fn):
        try:
            fn()
        except Exception:
            self.hub.log("diary entry failed: " + traceback.format_exc())

    def on_levelup(self, level_reached, map, active_seconds_in_prev_level, job, **_):
        def go():
            now = datetime.now()
            # the picture comes ~2 s into the animation (levelup_shot.py): render after it
            self.render_at = max(self.render_at, time.time() + 4)
            diary.add("levelup", diary.levelup_text(level_reached, map, active_seconds_in_prev_level),
                      level=level_reached, map=map, job=job, shots=self._shots("levelup", now),
                      took_s=active_seconds_in_prev_level)
        self._guard(go)

    def on_death(self, level, map, lost, pct_lost, box, killer, job, **_):
        def go():
            now = datetime.now()
            k = killer or {}
            who = k.get("label")
            diary.add("death", diary.death_text(level, map, lost, pct_lost, who),
                      level=level, map=map, job=job,
                      shots=self._shots("death", now, delays=(0, 2)),   # +2 s: ghost + grave
                      lost=lost, pct_lost=pct_lost, killed_by=who,
                      death_box=box or None,
                      killer_how=k.get("how"),
                      killer_guess=k.get("killer") if not who else None,
                      suspects=k.get("suspects") or None)
        self._guard(go)

    def on_job(self, old, new, level, map, **_):
        def go():
            now = datetime.now()
            diary.add("job", diary.job_text(old, new, level, map),
                      level=level, map=map, job=new,
                      shots=self._shots("job", now, delays=(0, 10)),
                      job_from=old, job_to=new)
        self._guard(go)

    def on_enter(self, map, level, job, **_):
        def go():
            if map and diary.mark_place(map):
                diary.add("first_visit", diary.first_visit_text(map), level=level,
                          map=map, job=job, shots=self._shots("first_visit", datetime.now(), delays=(3,)))
        self._guard(go)

    def on_moment(self, level, map, job, active_total, **_):
        def go():
            if diary.moment_due(active_total):
                diary.add("moment", diary.MOMENT_TEXT, level=level, map=map, job=job,
                          shots=self._shots("moment", datetime.now()))
        self._guard(go)

    def on_first_kill(self, mob, map, level, job, shots, caught, text, **_):
        def go():
            self.render_at = max(self.render_at, time.time() + 3)     # after the next-look picture
            diary.add("first_kill", text, level=level, map=map, job=job, shots=shots,
                      mob=mob["name"], mob_id=mob["id"], boss=bool(mob.get("boss")), caught=caught)
        self._guard(go)
