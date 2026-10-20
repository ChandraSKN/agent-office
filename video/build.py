"""Intro card + 1.3x office footage + outro card, with Buddy's voice-over placed per segment -> agent-office-demo.mp4"""
import json, subprocess
SPEED = 1.0
segs = json.load(open("segments.json"))
L = {n: secs for n, _, secs in segs}
intro = L["intro"] + 1.5
story = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", "raw.webm"])) / SPEED
outro = L["outro"] + 2.5
inputs, delays = [], []
for i, (n, start, secs) in enumerate(segs):
    at = 0.5 if n == "intro" else intro + story + 0.5 if n == "outro" else intro + start / SPEED + 0.3
    inputs += ["-i", f"vo_{n}.wav"]
    delays.append(f"[{i + 3}:a]adelay={int(at * 1000)}:all=1[a{i}]")
k = len(segs)
filt = (f"[0:v]scale=1280:720,setsar=1,fps=25,format=yuv420p[v0];"
        f"[1:v]drawbox=x=494:y=638:w=140:h=24:color=0xE4E3E9:t=fill:enable='gte(t,180.8)',drawbox=x=44:y=659:w=230:h=26:color=0xE2E0E6:t=fill:enable='gte(t,180.8)',setpts=PTS/{SPEED},scale=1280:720,setsar=1,fps=25,format=yuv420p[v1];"
        f"[2:v]scale=1280:720,setsar=1,fps=25,format=yuv420p[v2];"
        f"[v0][v1][v2]concat=n=3:v=1:a=0[v];" + ";".join(delays) + ";"
        + "".join(f"[a{i}]" for i in range(k)) + f"amix=inputs={k}:normalize=0,volume=1.0,apad=whole_dur={intro + story + outro:.2f}[a]")
cmd = ["nice", "-n", "15", "ffmpeg", "-v", "error", "-y", "-threads", "4",
       "-loop", "1", "-t", f"{intro}", "-i", "intro.png", "-i", "raw.webm", "-loop", "1", "-t", f"{outro}", "-i", "outro.png",
       *inputs, "-filter_complex", filt, "-map", "[v]", "-map", "[a]",
       "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-t", f"{intro + story + outro:.2f}",
       "agent-office-demo.mp4"]
subprocess.run(cmd, check=True)
print(f"{intro + story + outro:.0f}s")
