---
name: find-backing-track
description: Use when the user wants to learn a song on guitar, match a guitar tone, or asks for a backing track, isolated guitar track, or guitar stem for a specific song. Locates and confirms a guitar-only reference recording for tone matching, and optionally a minus-guitar playalong track for practice, then records the confirmed source(s) as a session target file.
---

# Find backing track

You are helping the user find real, listenable audio for a specific song so they can either learn the guitar part or (in a later release of this plugin) match its tone with Guitarix. This skill governs the whole search-confirm-record flow. Follow it in order — do not skip the confirmation step, do not write the target file before the user has confirmed a source, and do not guess at a local file path.

## 1. Reference vs. playalong — get this straight before you do anything else

There are two different things you might be looking for, and confusing them is the single most common way this skill goes wrong:

- **Reference** — an isolated or solo guitar recording (guitar and only guitar, or as close to it as you can get: an official stem, a DI, a multitrack solo). This is what lets you or a future tone-matching tool actually hear the guitar's timbre without a drum kit, bass, and vocal smeared on top of it. **This is the one that matters and the one you should always be looking for.**
- **Playalong** — a "minus guitar" backing track: the rest of the band with the guitar removed or turned down, meant for someone to play along with live. This is a nice-to-have for practicing, but it tells you nothing about tone, because the guitar isn't in it.

Default behavior: always search for a **reference** track. Treat a **playalong** track as a bonus you offer alongside it, never as a substitute for it. If the user only asks for a "backing track" to jam over, that's a playalong request — go find that, but still mention that if they ever want the tone matched, they'll want an isolated guitar reference too. If the user asks to "learn the guitar part," they need the reference, not a playalong, even though a playalong is fun to note as a bonus.

## 2. Pin down WHICH tone before you search

A song is not one guitar tone. Before running any search, nail down what the user actually wants, either from what they already told you or by asking:

- **Artist** and **song title**, spelled correctly (don't guess a cover band's version for a well-known original unless the user asked for that cover specifically).
- **Which section/part** of the song: the main riff, verse rhythm, chorus rhythm, the solo, a clean intro, etc. Different sections can use completely different tones (clean intro vs. distorted chorus vs. lead solo), so "the guitar tone from this song" is underspecified until you know which part.
- **Which version**: studio album cut vs. a specific live recording vs. a specific reissue/remaster. Tones can differ meaningfully between these. Default to the studio album version unless the user says otherwise.

Do not fire off searches on a vague request. If the user just says "find me a backing track for Enter Sandman," ask a short clarifying question (which section, which version) unless the context makes it obvious (e.g., they've been talking about learning the intro riff for the last three messages — then it's fine to proceed on that basis and simply state your assumption).

## 3. Search phrasings

Once you know artist, song, and section, run searches in roughly this order, adapting phrasing to what the search tool returns:

**For the reference (isolated guitar), try in order:**
1. `"<artist> <song>" isolated guitar track`
2. `"<artist> <song>" guitar only`
3. `"<artist> <song>" guitar stem`
4. `"<song>" isolated guitar <artist>`
5. `"<artist> <song>" multitrack`

**For a playalong (minus-guitar), if you're also looking for one:**
1. `"<artist> <song>" backing track no guitar`
2. `"<artist> <song>" guitarless backing track`

Don't stop at the first phrasing if it returns nothing useful — work down the list. Feel free to add the section name (e.g. "solo," "intro") to any of these when the user cares about a specific part, and to try artist-name variants (band name vs. featured guitarist's name) when relevant.

## 4. Judge source quality

Not everything a search turns up is usable. Consult `references/sources.md` in this skill directory for the full curated list of good sources, how to search each one, and their reliability — read it before or during your search, don't work from memory alone. In short, prefer, roughly in this order:

1. Official stems or remix-contest multitrack releases (artist/label-published, or DAW project files from a sanctioned remix contest).
2. The Cambridge-MT "Mixing Secrets" free multitrack library (`www.cambridge-mt.com/ms/mtk/`) — real multitrack recordings released for practice/education, often with a clean guitar stem.
3. Rhythm-game stem rips (Rock Band, Guitar Hero, etc.) — good isolation quality, but only ever suggest this source if the user already owns the game and the track, since ripping assets from a game you don't own is not something you should point someone toward. Say so explicitly when you suggest it.
4. Dedicated isolated-guitar YouTube channels that specialize in publishing guitar-only extractions or DI tracks for songs.
5. The user's own DI recording or stems, if they have them — always usable, no quality judgment needed, ask if they have one before doing anything else if the situation suggests they might (e.g. they're a working musician or have mentioned recording gear).

**Reject** anything that is:
- A full mix (you can hear drums, bass, or vocals along with the guitar).
- A live audience recording (crowd noise, phone-mic quality, bleed from the PA).
- A cover version, unless the user explicitly asked for that cover.
- Audible vocals or drums bleeding into an otherwise-labeled "isolated" track — titles lie; see the verification step below.
- Under about 30 seconds of continuous guitar audio — too short to be useful as a reference.
- Low-bitrate audio (roughly below 128kbps) — audible artifacts will corrupt tone judgments.
- **Behind a paywall, membership, or purchase.** Free sources only, by default. Do not present paid options in the numbered list. The one exception: if you have found NO usable free option after working the full search list and fallback ladder, you may mention that a paid option exists — clearly labeled with its price/membership requirement, never as a recommendation — and let the user decide entirely on their own. Registration-required-but-free is acceptable, but say so up front.

## 5. Verify every candidate with WebFetch — never trust the snippet

Search result snippets and titles are frequently wrong or exaggerated ("100% isolated guitar!!" on a track that's actually a full mix with the vocal turned down). Before you present ANY candidate to the user, WebFetch the page yourself and check what's actually there: the real title, the actual description, comments that confirm or dispute the isolation claim, stated duration, and file/stream quality if listed. Do this for every candidate you're considering presenting, not just the one you like best. If WebFetch can't get useful signal from a page (e.g. it's a page that doesn't render text), say so honestly rather than presenting it as verified.

Browser automation tools (if connected in this session) are a nice-to-have here — they can let you actually preview audio or scroll a page a plain fetch can't render — but they are never required. Design your search and verification flow to work with WebSearch and WebFetch alone; treat any browser tool as an optional enhancement on top of that, not a dependency.

## 6. Present 2–3 numbered options and ask — never proceed unconfirmed

Once you have verified candidates, present **2 to 3** numbered options (fewer than 2 only if you truly can't find more — see the fallback ladder below). For each option give:

1. **Title** (the real one, from verification, not just the search snippet).
2. **Source and URL.**
3. **Duration.**
4. **Why it fits** — what makes this a good match for what the user asked for (e.g. "official 2019 remix-contest stem, isolated bridge-pickup rhythm guitar for the whole song").
5. **One honest caveat** — something imperfect about it, stated plainly (e.g. "there's a faint click track bleeding through at low volume," "this is the live 2003 tour tone, not the studio one," "only covers the first 90 seconds of the track").

Then ask the user which one they want (or whether none of them work and you should keep looking). **Do not treat any candidate as chosen until the user says so.** Do not write anything to disk before this confirmation happens.

If you also found a playalong candidate, mention it as a separate, clearly-labeled bonus option — don't blend it into the numbered reference list, since it answers a different need (see §1).

## 7. Let the user retrieve the file themselves

You do not download files, bypass any access control, or fetch audio content directly into the user's filesystem yourself. Once the user has picked an option, they retrieve it themselves using their own browser or download tool, however they normally would. Your job is then to ask them for the local file path where they saved it.

If it would help, you may offer to check `~/Downloads` for the newest audio file — but only after asking the user's go-ahead first (e.g. "want me to check if it's the newest file in your Downloads folder?"). If they say yes, run something like:

```
ls -t ~/Downloads/*.{mp3,wav,flac,m4a,ogg,aac} 2>/dev/null | head -5
```

(adjust the glob to whatever audio extensions make sense) and show them the newest few candidates rather than silently assuming the top one is correct — let them confirm which file it actually is.

Whatever path the user gives you (or confirms from the Downloads listing), verify it actually exists before moving on — e.g. `test -f "<path>"` or an `ls` on it — and tell the user plainly if it doesn't, so they can correct it. Do not write a target file that points at a path you haven't confirmed exists.

## 8. Write the target file

Once the user has confirmed both the source and a verified local path, write a target file recording the session's target, exactly matching this schema:

```json
{
  "schema": "gx-mimic/target/1",
  "song": "<song title>",
  "artist": "<artist name>",
  "section": "<which tone: song section plus what the guitar is doing, e.g. \"verse rhythm\", \"solo\", \"clean intro\">",
  "role": "reference",
  "source": {
    "url": "<url of the confirmed reference source>",
    "title": "<verified real title>",
    "kind": "<content kind: \"isolated-guitar\" for a true reference; \"minus-guitar\" only ever for the playalong entry>"
  },
  "local_path": "<confirmed local path to the reference audio file>",
  "playalong": {
    "url": "<url of the playalong source, or null if none was found/wanted>",
    "local_path": "<confirmed local path to the playalong audio file, or null>"
  },
  "confirmed_at": "<ISO-8601 timestamp of confirmation>",
  "notes": "<anything worth remembering: caveats from the option you presented, section/version specifics, etc.>"
}
```

`role` is always the literal string `"reference"` in this file — it marks the main entry as isolated-guitar audio suitable for tone measurement, as opposed to the optional `playalong` entry (guitar removed, for practice). The later analysis step reads `role` and `source.kind` to sanity-check that it was handed the right kind of audio, so never write `"reference"` for a minus-guitar track. `section` carries everything about *which tone* the user chose, in plain language: the song section plus what the guitar is doing there (from step 2).

Compute the session slug as `artist-song`, lowercased, with all runs of whitespace/punctuation collapsed to single hyphens (e.g. "Green Day" + "Basket Case" → `green-day-basket-case`). Write the file to:

```
~/.local/state/gx-mimic/sessions/<slug>/target.json
```

(honor `$XDG_STATE_HOME` in place of `~/.local/state` if it's set in the environment, since that's the documented default state directory for this tool). Create the `sessions/<slug>/` directory first if it doesn't exist yet — nothing else in this early release of the plugin creates it for you.

After writing the file, tell the user plainly that the target has been recorded and that actual tone analysis and preset-building arrive in a future release of this plugin — this skill's job is just to lock in a good reference (and optional playalong), not to analyze it yet.

## 9. Boundaries

Stay strictly within sources the user is actually entitled to use:

- Never suggest bypassing a paywall, DRM, or a site's terms of service to get at audio.
- Never suggest or use stream-ripping tools or services (e.g. YouTube-to-MP3 converters), even if they'd technically produce a usable file. This applies regardless of how the user frames the request.
- The rhythm-game-stem-rip source category is conditional on the user already owning that game and track — say so when you suggest it, and don't suggest it otherwise.

If you genuinely can't find a usable reference after working through the search phrasings and the source-quality ladder, fall back in this order rather than giving up or reaching for a disallowed source:
1. Point the user at multitrack libraries they may not have thought of (Cambridge-MT, official stem/remix-contest archives) even if the specific song isn't there — sometimes similar-era songs from the same artist are.
2. If they own the relevant rhythm game and track, suggest that as a source.
3. Suggest the user record about 30 seconds of themselves playing the riff/section through the tone they're chasing — imperfect, but gives something real to work from.
4. As a last resort, suggest any recording with a broadly similar tone (same amp/pickup family, similar genre and era) as a rough stand-in, being explicit that it's an approximation and not the actual song's tone.

Always be honest with the user about which rung of this ladder you're on and why — don't present a fallback as if it were a proper match.
