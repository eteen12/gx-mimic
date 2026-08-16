# Backing track sources

Reference material for the `find-backing-track` skill: where to look for guitar-only reference audio and minus-guitar playalong tracks, how to search each source, and how reliable each one tends to be. Consult this file while searching — don't rely on memory of it.

## Reference vs. playalong, at a glance

| | Reference (isolated guitar) | Playalong (minus guitar) |
|---|---|---|
| Contains | Guitar, and as close to only guitar as possible | Everything except the guitar (drums, bass, vocals, keys) |
| Purpose | Hearing/measuring the actual tone | Practicing along with the band, live |
| Priority | **Always the primary goal** | Bonus, offer alongside if found |
| Typical search words | "isolated guitar", "guitar only", "guitar stem", "DI", "multitrack" | "backing track no guitar", "guitarless backing track", "minus guitar" |
| A track's presence substitutes for the other? | No | No — never treat a playalong as if it answers a reference request |

## Source list

### 1. Official stems / remix-contest multitrack releases
**Good for:** the highest-confidence source there is — audio straight from the artist or label, or from a sanctioned remix competition where the label released individual stems (often as WAV/AIFF, sometimes as full DAW project files). When one of these exists for the song in question, prefer it over everything else.
**How to search:** `"<artist> <song>" official stems`, `"<artist> <song>" remix contest stems`, `"<artist> <song>" stems download`, plus the artist/label name + "stems" on whatever platform they used to host the contest (often Splice, Indaba Music archives, or the label's own site).
**Reliability:** Very high when found — but rare. Most songs never had a public stem release. Don't spend excessive search budget here if the first couple of phrasings turn up nothing; move down the ladder.

### 2. Cambridge-MT "Mixing Secrets" free multitrack library
**URL:** `www.cambridge-mt.com/ms/mtk/`
**Good for:** real, full multitrack recordings released specifically for mixing practice and education — many rock/pop/metal songs (mostly by independent or emerging artists rather than chart hits) include a clean, isolated guitar stem. This is the most reliable *repeatable* source in this list because it's a stable, purpose-built library rather than something that might get taken down.
**How to search:** browse the library's own index/genre listing on the site (it's organized by song, with each song's available stems listed), or search `site:cambridge-mt.com <keyword>` / `cambridge-mt mixing secrets <genre or song>`. Because this library skews toward lesser-known artists, don't expect to find a specific famous song here — it's most useful as a *fallback* source (see the skill's fallback ladder) or when the user is open to a similar-tone substitute.
**Reliability:** High for what it has; low coverage of well-known commercial songs.

### 3. Rhythm-game stem rips (Rock Band, Guitar Hero, etc.)
**Good for:** clean, well-isolated guitar audio for a large catalog of well-known commercial songs, since these games shipped separated instrument stems for their gameplay engine.
**How to search:** `<song> <artist> guitar only rock band`, `<song> guitar hero isolated guitar`.
**Reliability:** Audio quality is generally good, but **only ever suggest this source if the user already owns the game and the specific track** — say this explicitly whenever you suggest it. Extracting game assets you don't own crosses into territory this skill won't point people toward (see Boundaries in SKILL.md).

### 4. Dedicated isolated-guitar YouTube channels
**Good for:** channels that specialize in publishing guitar-only extractions, DI tracks, or "guitar only" mixes for well-known songs — often run by guitar teachers or gear demo enthusiasts. Coverage of popular songs is much better here than the Cambridge-MT library.
**How to search:** the search phrasings in SKILL.md §3 (`"<artist> <song>" isolated guitar track`, etc.) will usually surface these channels directly. Once you find a channel that looks reliable, it's worth checking whether they've covered other sections/songs the user might want later.
**Reliability:** Variable — this is exactly why SKILL.md §5 requires WebFetch verification of every candidate before you present it. Titles overclaim ("100% isolated!") more often here than anywhere else on this list; check the description and comments for corroboration, and listen/inspect duration and quality signals where you can.

### 5. The user's own DI recording or stems
**Good for:** anything the user personally recorded — a DI (direct input) of their own guitar, or stems from their own multitrack session. Always usable with no source-quality judgment needed, since there's no isolation-claim to verify.
**How to search:** n/a — ask the user directly if they have one, especially if context suggests they might (they've mentioned recording gear, a DAW, or being in a band).
**Reliability:** As good as whatever they recorded. If they have one, it typically beats searching for anything else.

## Reject list

Do not present any of the following as a reference candidate:

- **Full mixes** — you can hear drums, bass, or vocals along with the guitar.
- **Live audience recordings** — crowd noise, phone-mic quality, PA bleed.
- **Cover versions**, unless the user explicitly asked for that specific cover.
- **Mislabeled "isolated" tracks** where vocals or drums actually bleed through — verify, don't trust the title.
- **Under ~30 seconds of continuous guitar audio** — too short to judge a tone from.
- **Low-bitrate audio** (roughly below 128kbps) — compression artifacts will corrupt any tone judgment made from it.

## Worked examples

### Classic rock — "Sweet Child O' Mine," Guns N' Roses
Section requested: the opening riff/intro (Slash's clean-into-dirty tone). Search order: `"Guns N' Roses Sweet Child O' Mine" isolated guitar track` → `"Guns N' Roses Sweet Child O' Mine" guitar only` → `"Sweet Child O' Mine" isolated guitar Slash`. Expect to find several guitar-lesson/gear-demo YouTube channels with isolated or near-isolated extractions of the intro riff — this is a heavily-covered, heavily-analyzed song, so the isolated-YouTube-channel tier (source #4) is the likely winner. Verify each candidate's description/comments for confirmation the drums/bass are actually absent before presenting it, since this song's fame means low-effort "isolated" uploads with audible bleed are common too.

### 90s punk/alternative — "Basket Case," Green Day
Section requested: verse/chorus rhythm guitar (Billie Joe Armstrong's driving distorted rhythm tone). Search order: `"Green Day Basket Case" isolated guitar track` → `"Green Day Basket Case" guitar only` → `"Green Day Basket Case" guitar stem` → `"Basket Case" isolated guitar Green Day` → `"Green Day Basket Case" multitrack`. Also worth trying the rhythm-game angle (`Basket Case guitar hero isolated guitar`) since this is exactly the kind of well-known commercial single those games covered — flag the ownership caveat if you suggest it. If nothing isolated turns up, Cambridge-MT is very unlikely to have this specific major-label song (source #2 skews toward lesser-known artists), so it's not worth spending much search budget there for this one.

### Modern metal — "Chop Suey!," System of a Down
Section requested: main riff (Daron Malakian's syncopated, palm-muted verse tone). Search order: `"System of a Down Chop Suey" isolated guitar track` → `"System of a Down Chop Suey" guitar only` → `"System of a Down Chop Suey" guitar stem` → `"Chop Suey" isolated guitar Daron Malakian` → `"System of a Down Chop Suey" multitrack`. Modern/nu-metal songs like this one are popular targets for gear-demo channels reproducing the tone (amp sim and pedal demos often post an isolated DI or reamped clip alongside the demo) — check those in addition to plain "isolated guitar" uploads, and verify carefully since demo channels sometimes post *their own* replayed version rather than the original recording, which is not what you want to hand back as "the reference."
