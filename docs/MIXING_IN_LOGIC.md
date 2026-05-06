# Mixing the BTC sonification in Logic Pro

`btc-sonify` produces a multi-track MIDI file. MIDI is just instructions —
the depth, character, and emotion all come from how it's played and mixed.
This guide walks through turning the raw MIDI into a finished track in
Logic Pro.

It assumes you've run `btc-sonify` with the cinematic palette (the default
configuration that produces five tracks: melody, harmony, bass, voice,
percussion).

## 1. Import and instrument assignment

Drag the `.mid` file into an empty Logic project. Logic auto-assigns a
sensible patch per channel based on the General MIDI program numbers
written by `midi_writer.py`. You'll typically get something like:

| Track | Default patch | Role |
|-------|---------------|------|
| 1 | Bright Synth Brass | Melody — close-price contour |
| 2 | Aurora Waves | Harmony — range-driven chords |
| 3 | Taureg Moon Bass | Sub-bass — close one octave down |
| 4 | Classic Choir | Voice — sustained "lead vocal" line |
| 5 | Kick 1 - Tough Kit | Percussion — volume / volatility cues |

**Two fixes before you do anything else:**

1. **The default percussion patch is kick-only.** Your MIDI uses the full
   GM drum map (kick on note 36, snare 38, hi-hat 42, etc.), so most
   percussion notes will be silent. Select track 5, press `Y` to open
   the Library, and choose **Acoustic Drums → SoCal / Brooklyn / Blue
   Ridge**, or **Electronic Drums** for a more synthwave feel. Any full
   kit will respond to the whole drum map.
2. **Let Logic follow the MIDI's tempo automation.** Symphony bounces
   ship with an automated tempo lane: each movement has its own BPM,
   and (when rubato is enabled) tempo *breathes* within each movement —
   slowing into structural pivots, pushing through trends. Confirm the
   transport reads `AUTO TEMPO` rather than a fixed BPM. If Logic
   ignored the embedded tempo on import, click the tempo display and
   pick **Use Tempo from MIDI File** (or `Tempo Track ▸ Show Tempo
   Track`, which exposes the per-bar curve so you can see the rubato
   for yourself).

## 2. Audition patches

For each track, select it, press `Y`, and browse the Library. Hit play
with cycle (`C`) enabled so you can audition while it's running. A
palette that's worked well for BTC's modal/searching character:

| Track | Try | Why |
|-------|-----|-----|
| Melody | Felt Piano, Vibraphone, Mystic Vibes | Bright Synth Brass is harsh on dense melodies. Softer attack reads as melodic without piercing. |
| Harmony | The Highlander, Cinematic Strings | Wide, slow-moving bed under the melody. |
| Bass | Deep 808 Bass | Sub presence without muddying the mid-range. |
| Voice | Breathless Space, Beyond Deep Skies | Long pad acts like a lead vocal floating above the mix. |
| Percussion | Blue Ridge, SoCal | Acoustic kit grounds the synths in something organic. |

Rule of thumb: pair *one* bright/percussive element with everything
else soft. If melody is bright, voice should be diffuse. If melody is
soft (felt piano), voice can be more present.

## 3. Reverb send bus (the single biggest "depth" move)

MIDI rendered through soft synths sounds dry and sterile. A shared
reverb bus puts every melodic instrument in the same imagined room.

1. Press `X` to open the Mixer.
2. On the **Melody** channel strip, find the Sends section. Click an
   empty send slot → choose **Bus 1**. Logic auto-creates an aux track.
3. On the new aux track's first insert slot, load **ChromaVerb**.
   Choose preset **"Cathedral"** or **"Large Hall"**. Leave the aux's
   wet/dry balance at 100% wet (the send level controls how much each
   track contributes).
4. Add a Bus 1 send to each melodic track, with these levels:

   | Track | Send to Bus 1 |
   |-------|---------------|
   | Voice | **−8 dB** (most reverb — sits furthest back) |
   | Melody | **−15 dB** |
   | Harmony | **−18 dB** |
   | Bass | **no send** (reverb on sub = mud) |
   | Percussion | **no send** (drums stay punchy) |

To set a send level: drag the small round knob next to the bus name,
or double-click the dB readout and type the value.

If you accidentally end up with multiple buses (Bus 2, Bus 3 — easy to
do while exploring), consolidate everything to Bus 1 and right-click
the unused auxes → Delete.

**What you should hear after this step:** instruments suddenly feel
like they're in the same physical space. Voice has a long shimmering
tail. Bass and kick still hit dry and tight.

## 4. Sidechain compression on Harmony

This is the move that makes the track feel like it has a pulse. The
kick triggers a brief volume duck on the harmony — your ear hears it as
groove without consciously noticing the compressor.

1. On the **Harmony** channel strip, insert **Dynamics → Compressor**.
2. Settings:
   - Circuit: **Studio VCA**
   - Ratio: **4:1** (try 3:1 if 4:1 feels too aggressive)
   - Threshold: **around −20 to −25 dB**
   - Attack: **5 ms**
   - Release: **150 ms**
3. Top-right of the plugin: **Side Chain** dropdown → choose your
   **Percussion** track.
4. **Critical:** filter the sidechain so it only listens to the kick.
   In the right-side filter section: **Filter On**, **Mode: LP**,
   **Frequency: 120 Hz**. Without this filter, snare and hat trigger
   the compressor too — you get constant ducking instead of rhythmic
   pumping.
5. Watch the gain reduction meter while playing. Goal: needle dances
   **3-5 dB down on every kick, returns to 0 between kicks**. Pinned =
   too much. Barely moving = too little. Adjust threshold to taste.

**Why it works:** the ear reads "ducks on every kick, opens between"
as groove. "Always compressed" is just quieter — same dB reduction,
completely different feel.

## 5. Bass cleanup

Sub-bass synths often emit content below 30 Hz that no real speaker
reproduces — it just eats headroom and makes everything else feel
weaker. A targeted EQ tightens the low end audibly.

1. On the **Bass** track, insert **EQ → Channel EQ**.
2. Enable **Band 1** (high-pass, leftmost). Set frequency to **30 Hz**,
   slope **12 or 24 dB/oct**. The curve should slope down on the left.
3. Enable **Band 2** (low shelf, second from left). Set **+2 dB at
   80 Hz** for warmth. (Optional but recommended.)
4. Optional: insert **Distortion → Overdrive**, drive ~10%, tone ~50%.
   Adds harmonic content so the bass remains audible on phones and
   laptops where the actual sub frequencies disappear.

You won't necessarily hear the bass change much — but the *rest of the
mix* will sound clearer because there's less mud underneath.

## 6. Master bus polish

This is the final pass. Glues the mix together and brings it up to
streaming-competitive loudness.

1. Click the **Stereo Out** channel strip (look for the one with
   "Mastering" written vertically — it's the master output before the
   final hardware out).
2. **Insert 1**: **Dynamics → Compressor**
   - Circuit: **Platinum Digital**
   - Ratio: **2:1**
   - Threshold: set so the gain reduction meter only catches **−2 to
     −3 dB on peaks** — not constant compression
   - Attack: **30 ms** (slow, lets transients through)
   - Release: **Auto**
3. **Insert 2**: **Dynamics → Adaptive Limiter**
   - Out Ceiling: **−1.0 dB** (leaves headroom for streaming services)
   - Gain: start at **+3 dB**, raise gradually while playing the
     loudest section until it sounds full but the limiter only catches
     peaks
   - Lookahead: default

After this, the mix should feel **glued** — five separate instruments
become one piece of music. Quiet moments rise, loud moments stay
controlled, the whole thing gets perceptibly louder without distorting.

## 7. Stereo widening

Pulls the pad and harmony layers apart so they fill the stereo field.
Bass and percussion stay center for punch — only widen the things that
should feel atmospheric.

1. On the **Harmony** track, insert **Imaging → Stereo Spread**.
   - Order: **4** or **5**
   - Lower Int.: **0.5**
   - Upper Int.: **0.7**
   - Lower Freq: **300 Hz** (don't widen low-mid content)
   - Upper Freq: **1500 Hz**
2. Same plugin on the **Voice** track, but Upper Int. **0.5** so it
   doesn't compete with harmony for stereo real estate.
3. **Do not** add Stereo Spread to bass, kick, or percussion — these
   need to stay locked in the center to anchor the mix.

A/B test: toggle the plugin's power button on/off while playing. With
it off the mix should sound smaller and more centered; with it on the
pads should feel like they wrap around your head, especially on
headphones.

## Final chain summary

By this point each track has:

| Track | Inserts | Send |
|-------|---------|------|
| Melody | (instrument only) | Bus 1 reverb @ −15 dB |
| Harmony | Compressor (sidechained to kick), Stereo Spread | Bus 1 reverb @ −18 dB |
| Bass | Channel EQ (HP 30 Hz, +2 dB shelf @ 80 Hz) | dry |
| Voice | Stereo Spread | Bus 1 reverb @ −8 dB |
| Percussion | (instrument only) | dry |
| **Bus 1 aux** | ChromaVerb (Cathedral) | — |
| **Stereo Out** | Compressor (2:1, gentle), Adaptive Limiter (−1 dB) | — |

That's a complete mixing pass. The same chain works as a starting
point for any `btc-sonify` output — copy this project as a template
and just swap the imported MIDI.

## Optional further moves

- **Subtle delay on Melody**: Stereo Delay, ~1/8 note, 15% feedback,
  20% wet — adds spatial echo without muddying.
- **Automation across movements**: if you used `--mode symphony`,
  draw automation on the master gain to dip slightly between movements
  so each one re-enters with impact.
- **Volume rides on Voice**: automate the voice fader up during
  bullish movements (high close prices) and down during bearish ones —
  emotional contour mirrors the data.
- **Bounce stems**: File → Export → All Tracks as Audio Files. Useful
  if you want to remix elsewhere or keep the source separated.

## Saving the chain as a template

Once you're happy:
- **File → Save as Template** to make a `.logicx` template you can
  reuse for future sonifications. Empty out the regions but keep the
  channel strips, sends, and bus FX.
- Or keep the current project as `btc-symphony-cinematic-template.logicx`
  and duplicate it for each new run.
