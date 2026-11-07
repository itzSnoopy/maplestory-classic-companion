# components/gains: ledger of the message corner, checked against the EXP bar

Tracker side only. Writes `history/gains.csv` (one row per notification line: exp with party bonus and own flag, mesos,
items `name,count,tab`, other lines as OCR text) and `history/gain_check.csv` (one row per burst: EXP the notices add up
to vs the bar's rise, verdict `ok | bar_more | notif_more | overflow | levelup`). Purpose: a baseline for the OCR of the
EXP notices and to catch a double read (`notif_more`) or a missed line (`bar_more`).

**May import:** `hub`, `base`. **Never:** another component, `engine`.
Files: `component.py` (Burst, verdict, GainsComponent). Test: `python dev/gains_sim.py FIXTURE_DIR [OUT]` (fixture needs vitals.csv).
Subscribes: exp_gain, meso_gain, item_gain, msg_other, fact (exp), poll, service_stop.
