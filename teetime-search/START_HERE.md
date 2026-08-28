# Start here 👋

A friend sent you this — it's a golf tee-time finder that runs inside
Claude Code. It searches the booking sites you already use, shows everything
open near you on one sheet, and can text you when tee times open up for the
days you usually play. You don't need to know how any of it works: Claude
sets it up for you.

## One-time setup (about 5 minutes)

There is nothing to install by hand — Claude sets everything up.

1. **Put this folder somewhere permanent** — for example your home folder —
   not in Downloads where it might get cleaned up.

2. **Open Terminal** (press Cmd+Space, type "Terminal", press Enter),
   then type this and press Enter (adjust the path if you put the folder
   somewhere else):

   ```
   cd ~/teetime-search && claude
   ```

   (If your Mac pops up an offer to install "command line developer
   tools" at any point, click Install — that's normal.)

3. **Paste this to Claude** and let it drive:

   > Read SKILL.md in this folder. Install this folder as a skill at
   > ~/.claude/skills/teetime-search, install whatever it needs, start the
   > service, and show me a demo tee sheet so I can see it working. Then
   > walk me through connecting my golf booking accounts, and set up the
   > watcher so it texts me when tee times open for the days I usually play.

Claude will ask you a few questions along the way — which days you usually
play, how many people are in your group, your ZIP code, your phone number
for texts. Answer in plain English; that's the point.

## After setup

Just open Claude Code and talk to it like a person:

- "Where can I play Saturday morning?"
- "Anything under $40 within 15 miles on Sunday?"
- "Connect my foreUP account."

And if you set up the watcher, you don't even have to ask — you'll get a
text the morning your courses open their booking window.

## Two honest warnings

- Connecting a booking account means automated access with your own login,
  which may be against that site's terms of service. Claude will tell you
  this before storing anything; the risk is yours to accept or skip.
- Your passwords stay in your Mac's keychain, on your machine, full stop.
  Nothing is uploaded anywhere.
