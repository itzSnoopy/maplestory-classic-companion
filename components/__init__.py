"""Components layer: one folder per feature; a component may use hub, gamedata and base, never another component.

COMPONENTS is the registry, like engine/sources/__init__.py is for sources: "module:Class" for every
component that runs in the tracker process. They are loaded by name (load_all) so importing
`components.diary.diary` in the helper doesn't pull in every feature. Order matters: handlers run in
this order, so a component that fills a payload (killers) or whose services others ask (skillbar's
live feed) comes before the ones that read it.
"""
import importlib

COMPONENTS = [
    "components.killers.component:KillersComponent",     # death_guess: who killed you
    "components.diary.component:DiaryComponent",         # the diary beats, camera button, notes
    "components.skillbar.component:SkillbarComponent",   # live.json feed: HP/MP, regen, training costs
    "components.album.component:AlbumComponent",         # monster album (after the live feed's exp_rise)
    "components.party.component:PartyComponent",         # party link thread
]


def load_all():
    """The component classes, in order."""
    out = []
    for ref in COMPONENTS:
        mod, cls = ref.split(":")
        out.append(getattr(importlib.import_module(mod), cls))
    return out
