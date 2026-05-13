# codex-turn-sound

Play a short custom chime when a Codex turn ends.

This uses Codex's native `notify` command, so it does not depend on a Skill being selected in a conversation. The installer writes the correct absolute path for each machine into `~/.codex/config.toml`.

## Install

Install directly from GitHub:

```sh
npm exec --yes --package github:verycafe/Coling codex-turn-sound -- install
```

Restart Codex or open a new Codex session after installing.

## Test

Play the sound manually:

```sh
~/.codex/codex-turn-sound/app/bin/codex-turn-sound turn-ended
```

Then test the Codex lifecycle:

1. Open a new Codex session.
2. Send a tiny prompt, for example: `只回复“测试完成”`
3. After Codex finishes the turn, you should hear the bundled chime.

## How It Works

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

```sh
npm exec --yes --package github:verycafe/Coling codex-turn-sound -- uninstall
```

The uninstaller restores the previous `notify` command when one was present.

## After Publishing to npm

If this package is published to the npm registry as `codex-turn-sound`, install and uninstall become:

```sh
npx --yes codex-turn-sound install
npx --yes codex-turn-sound uninstall
```

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
