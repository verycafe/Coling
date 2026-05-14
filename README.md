# codex-turn-sound

Play a short custom chime when a Codex turn ends.

## One-line Install

Run this command:

```sh
npm exec --yes --package github:verycafe/Coling codex-turn-sound -- install
```

Then restart Codex or open a new Codex session.

## Quick Test

Play the sound manually:

```sh
~/.codex/codex-turn-sound/app/bin/codex-turn-sound turn-ended
```

Then test the Codex lifecycle:

1. Open a new Codex session.
2. Send a tiny prompt, for example: `只回复“测试完成”`
3. After Codex finishes the turn, you should hear the bundled chime.

## How It Works

This uses Codex's native `notify` command, so it does not depend on a Skill being selected in a conversation.

The installer updates `~/.codex/config.toml` with your real home directory:

```sh
notify = ["/Users/you/.codex/codex-turn-sound/app/bin/codex-turn-sound", "turn-ended"]
```

Codex calls this command when a turn ends. The installer copies the runtime into `~/.codex/codex-turn-sound/app/`, so the tool keeps working even when the temporary `npm exec` download is removed.

If a previous Codex `notify` command existed, this tool preserves it and runs it before playing the sound.

## Change the Sound

Use a macOS system sound by name:

```sh
CODEX_TURN_SOUND=Glass ~/.codex/codex-turn-sound/app/bin/codex-turn-sound turn-ended
```

Use any audio file supported by `afplay`:

```sh
CODEX_TURN_SOUND=/absolute/path/to/sound.wav ~/.codex/codex-turn-sound/app/bin/codex-turn-sound turn-ended
```

Disable sound for one run:

```sh
CODEX_TURN_SOUND=off ~/.codex/codex-turn-sound/app/bin/codex-turn-sound turn-ended
```

Install with a custom sound:

```sh
npm exec --yes --package github:verycafe/Coling codex-turn-sound -- install --sound /absolute/path/to/sound.wav
```

## Uninstall

Run:

```sh
npm exec --yes --package github:verycafe/Coling codex-turn-sound -- uninstall
```

The uninstaller restores the previous `notify` command when one was present.

## Develop Locally

Clone the repository:

```sh
git clone https://github.com/verycafe/Coling.git
cd Coling
```

Run checks:

```sh
npm test
```

Generate the bundled sound again:

```sh
python3 scripts/make_sound.py assets/soft-chime.wav
```
