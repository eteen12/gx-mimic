---
name: learn
description: Find a guitar reference/backing track for a song so you can learn it or match its tone.
---

The user wants to learn the following song on guitar: $ARGUMENTS

Follow the `find-backing-track` skill's full flow to locate a suitable reference (isolated/solo guitar) track for this song, and a playalong (minus-guitar) track if one is available. Do not skip steps in that skill — in particular, pin down which section/tone of the song is wanted before searching, verify every candidate source with WebFetch before presenting it, and present the user with numbered options to confirm before treating anything as final.

If the user's request is ambiguous about which song, artist, or section they mean, ask before searching.
