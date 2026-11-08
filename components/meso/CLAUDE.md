# components/meso: dev-only meso counter (parked until after 1.0)

A small floating window that counts meso pickups read from the message corner and checks them against
the inventory. Helper process only. **Dev only:** the menu entry "Meso counter (dev)" exists in source
runs and local test builds (`version.TEST_BASE` set), never in a GitHub release (`dev_build()`).

**Memory (local):** `.claude/handoff/components/meso.md`. **May import:** `hub`, `base`. **Never:** another component, `engine`.

## Files
| File | Role |
|---|---|
| `component.py` | `MesoComponent`: `ui(shell)` adds the PanelHost "meso" toggle when `dev_build()`; `attach` does nothing |
| `meso_ui.py` | `Ledger` (tails `history/gains.csv` kind=meso rows incrementally, survives `.csv.old` rotation), `MesoPanel` (tkinter, drag to move, Mark box) |

## Interfaces
- **Reads:** `history/gains.csv` (written by components/gains in the tracker; no events used).
- **Writes (DATA):** `history/meso_counter.json` (last mark: time + inventory value), `history/meso_marks.csv`
  (`t,time,estimate,actual,drift,pickups,picked_sum,map`; drift + = corner missed mesos, - = counted too many).
- **Settings:** helper_settings.json `meso` {enabled, locked, x, y}.

## Use
Turn on "Meso counter (dev)" in the helper menu, type the inventory meso + Enter, play, mark again when you check
the inventory. Shop spending, drops and quest mesos aren't pickups: mark after those.
