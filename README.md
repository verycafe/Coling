# codex-turn-sound

Play a short custom chime when a Codex turn ends.

This uses Codex's native `notify` command, so it does not depend on a Skill being selected in a conversation. The installer writes the correct absolute path for each machine into `~/.codex/config.toml`.

## Install

One-line install after the package is published to npm:

```sh
npx --yes codex-turn-sound install
```

One-line install directly from GitHub before publishing to npm:

```sh
npm exec --yes --package github:verycafe/Coling codex-turn-sound -- install
```

Manual install from a cloned repository:

```sh
git clone https://github.com/verycafe/Coling.git
cd Coling
./install.sh
```

Restart Codex or open a new Codex session after installing.

## Test

```sh
codex-turn-sound turn-ended
```

You should hear the bundled `assets/soft-chime.wav`.

To test the Codex lifecycle, start a new Codex session and ask it to answer a tiny prompt. The sound should play after the assistant finishes the turn.

## Change the Sound

Use a macOS system sound by name:

```sh
CODEX_TURN_SOUND=Glass codex-turn-sound turn-ended
```

Use any audio file supported by `afplay`:

```sh
CODEX_TURN_SOUND=/absolute/path/to/sound.wav codex-turn-sound turn-ended
```

Disable sound for one run:

```sh
CODEX_TURN_SOUND=off codex-turn-sound turn-ended
```

To make a custom generated sound and install it:

```sh
python3 scripts/make_sound.py assets/my-chime.wav
./install.sh --sound "$(pwd)/assets/my-chime.wav"
```

## Uninstall

```sh
npx --yes codex-turn-sound uninstall
```

The installer backs up `~/.codex/config.toml` before editing it and preserves any previous `notify` command by chaining it from the runtime wrapper.
