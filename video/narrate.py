"""Buddy's voice-over in his own Piper voice, made more human: sentence by sentence, a touch slower,
more expressive, with natural pauses. Writes vo_<name>.wav and segments.json; warns when a clip overruns its slot."""
import json, re, wave
from pathlib import Path
import numpy as np
from piper import PiperVoice, SynthesisConfig

HERE = Path(__file__).parent
VOICE = PiperVoice.load(str(Path.home() / ".local/share/desktop-buddy/voices/en_US-ryan-medium.onnx"))
CFG = SynthesisConfig(length_scale=1.07, noise_scale=0.75, noise_w_scale=0.9)  # ponytail: tuned by ear
PAUSE = {".": 0.45, "?": 0.6, "!": 0.5, "…": 0.7}

# (name, chapter title in caps.json where it starts (first time shown), or None for a card, text)
SEGMENTS = [
 ("intro", None, "Hey, I'm Buddy. Can I tell you about an idea we've been working on? "
   "In college, you learn to code. But nobody really teaches you how a company runs… "
   "Who owns what? What do you do when someone takes leave right before a demo? Or when two engineers just won't agree? "
   "So, here's the idea. An open source playground, where anyone who dreams of starting a startup can run a pretend company… "
   "with AI teammates who do real work, on a real product. Let me show you."),
 ("org", "The org chart", "So, this is our office. I'm the CEO. The founder tells me what we're building, and the CTO plans the work. "
   "Over there is the software team, and that side is the hardware lab. Down front, product, and operations."),
 ("goal", "The CEO's vision", "The founder wants Lumi, a lamp that knows when you're tired. Demo in ten days!"),
 ("kickoff", "Kickoff", "Now, does the CTO start handing out tasks? Nope. Not yet. "
   "First, a kickoff. Product, both team leads, and operations sit down with us. Who is it for? When's the demo? What's the budget?"),
 ("spec", "Spec review", "Then product writes a one page spec, with clear checks for done. And look… in the review, "
   "the hardware lead reads tired as a camping lamp! Software thinks it's a sleep app. "
   "So we fix it right there, in twenty minutes, not after two days of building. Then the founder says yes."),
 ("plan", "Leads plan → CTO delegates", "Only now do the leads split the work, and the CTO sends out the briefs."),
 ("leave", "Holidays happen", "Diwali week. Two people are off, so the backups pick up their work."),
 ("crunch", "Deadline pressure", "Then the deadline hits. Bugs, late parts, failed prints… see the stress bars? "
   "Late at night, the CTO sends the most tired person home. Tired people ship bugs."),
 ("conflict", "Conflict", "Backend wants to ship. The reviewer says no. "
   "So the CTO gets the facts, and decides with a simple rule. Blockers now, the rest later."),
 ("human", "The human stays in charge", "And you're always in charge. Anything risky waits for your yes."),
 ("ship", "Ship it", "And… Lumi ships, on time! Every decision is right there in the log, "
   "so afterwards, you can see what worked, and which role really felt like you."),
 ("outro", None, "So that's the dream. It'll be open source, and students will add the problems they actually face, "
   "so it keeps getting more real. "
   "And here's my favourite part. The founder can swap any agent for a real person. "
   "Say you're a student. You sit in the firmware chair, and your AI teammates act like real people. "
   "They ask for leave, they get stressed, they want promotions. So you're not just reading about a team. You're living it. "
   "Next, I'm building the full platform, with a shared, low cost setup, so any student can run their own company. "
   "And when you start a real one someday? You'll already know how it feels. I'm Buddy. Let's build it together!"),
]


def speak(text):
    rate, out = 22050, []
    for sentence in re.findall(r"[^.?!…]+[.?!…]*", text):
        if not sentence.strip():
            continue
        chunks = list(VOICE.synthesize(sentence.strip(), syn_config=CFG))
        rate = chunks[0].sample_rate
        out += [c.audio_int16_array for c in chunks]
        out.append(np.zeros(int(rate * PAUSE.get(sentence.strip()[-1], 0.35)), np.int16))
    return np.concatenate(out), rate


caps = json.loads((HERE / "caps.json").read_text())
first = {}
for t, title in caps["caps"]:
    first.setdefault(title, t)
SEGMENTS = [(n, None if c is None else first[c] + 0.1, text) for n, c, text in SEGMENTS]
starts = [s for _, s, _ in SEGMENTS if s is not None] + [caps["total"]]
meta = []
for name, start, text in SEGMENTS:
    pcm, rate = speak(text)
    with wave.open(str(HERE / f"vo_{name}.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm.tobytes())
    secs = len(pcm) / rate
    slot = starts[starts.index(start) + 1] - start if start is not None else 0
    print(f"{name:9} {secs:5.1f}s" + (f" / {slot:.1f}s" if slot else "") + ("  OVER" if slot and secs > slot else ""))
    meta.append([name, start, secs])
(HERE / "segments.json").write_text(json.dumps(meta))
