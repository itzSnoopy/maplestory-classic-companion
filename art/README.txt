Custom artwork for the helper.

icon.png  - the helper's icon (tray + Discord settings preview). Square PNG with a transparent
            background, 512x512 or larger is ideal. Shown inside the round badge whose ring
            color shows the tracker state. Restart the helper after changing it.
            Without it, the built-in placeholder (arrow + EXP bar) is used.

CURRENT icon.png (2026-10-23) = TEMPORARY placeholder: a pixelated Mano picture the user supplied
(placeholder_source.jpg). That is Nexon game art -> LOCAL ONLY:
  - never commit it, never upload it (Discord app art, websites, etc.)
  - do NOT regenerate discord_icon.png from it
Replace it with the artist friend's original art when that arrives (then delete
placeholder_source.jpg), and only then refresh discord_icon.png:
  python -c "import helper_ui as h; h.make_icon_image(h.GREEN, 512).save('discord_icon.png')"

discord_icon.png (project root) = Discord placeholder (2026-10-23): a pixelated, recolored maple
leaf with a PLACEHOLDER watermark, built by dev/make_leaf_placeholder.py from
art/fluent_maple_leaf_3d.png. Source: Microsoft Fluent Emoji "Maple leaf" (3D),
https://github.com/microsoft/fluentui-emoji , MIT License, Copyright (c) Microsoft Corporation.
Modified by us. Fine to upload/publish (keep this notice with copies). Not Nexon art.
