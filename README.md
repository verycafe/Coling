# codex-turn-sound

Play a short chime when a Codex turn ends.

## One-line Install

```sh
npm exec --yes --package='github:verycafe/Coling#v0.2.0' -- codex-turn-sound install
```

Restart Codex or open a new Codex session after installation.

Requirements: macOS, Node.js 22.9 or newer with npm, and Python 3.11 or
newer. Node.js 20.17 remains supported for legacy compatibility.

## Quick Test

```sh
"${CODEX_HOME:-$HOME/.codex}/codex-turn-sound/app/bin/codex-turn-sound" turn-ended
```

For a lifecycle test:

1. Open a new Codex session.
2. Send a tiny prompt, such as `只回复“测试完成”`.
3. Wait for Codex to finish the turn.

## How It Works

The installer uses Codex's native root-level `notify` setting. It installs a
self-contained runtime under `${CODEX_HOME:-$HOME/.codex}/codex-turn-sound/`
and writes an absolute command path into `config.toml`.

Existing notification commands are preserved. Codex's JSON event payload is
forwarded to them without blocking sound playback. Installation uses a lock,
unique backup, atomic writes, and rollback if any commit step fails.

The tool also recognizes the `--previous-notify` chain used by Codex Computer
Use, so upgrades and uninstalls preserve that outer notification wrapper.

## Change the Sound

Install with any audio file supported by macOS `afplay`:

```sh
npm exec --yes --package='github:verycafe/Coling#v0.2.0' -- codex-turn-sound install --sound /absolute/path/to/sound.wav
```

The installer validates and copies the custom sound into its managed runtime,
so moving the original file later does not break playback. Updates preserve the
managed custom sound unless `install --reset-sound` is used.

Use a macOS system sound for one manual run:

```sh
CODEX_TURN_SOUND=Glass "${CODEX_HOME:-$HOME/.codex}/codex-turn-sound/app/bin/codex-turn-sound" turn-ended
```

Disable one manual run:

```sh
CODEX_TURN_SOUND=off "${CODEX_HOME:-$HOME/.codex}/codex-turn-sound/app/bin/codex-turn-sound" turn-ended
```

## Update

Run the pinned installation command again:

```sh
npm exec --yes --package='github:verycafe/Coling#v0.2.0' -- codex-turn-sound install
```

## Uninstall

For every version, including an older installed runtime, use the pinned
uninstaller:

```sh
npm exec --yes --package='github:verycafe/Coling#v0.2.0' -- codex-turn-sound uninstall
```

It restores the previous notification chain and removes the managed runtime and
state files. A uniquely named `config.toml.bak-uninstall-*` backup is retained.
The zero-byte `${CODEX_HOME:-$HOME/.codex}/.codex-turn-sound.lock` file is also
retained intentionally so concurrent future installs always coordinate on the
same lock inode.

After updating to v0.2.0, the local offline command is also available:

```sh
"${CODEX_HOME:-$HOME/.codex}/codex-turn-sound/app/bin/codex-turn-sound" uninstall
```

## Diagnose

```sh
"${CODEX_HOME:-$HOME/.codex}/codex-turn-sound/app/bin/codex-turn-sound" doctor
```

Use `doctor --json` for structured results or `doctor --play` to validate
playback. The command checks the active config chain, runtime version, managed
sound, checksum, and macOS audio support without recording Codex event payloads.

## Develop Locally

```sh
git clone https://github.com/verycafe/Coling.git
cd Coling
npm test
npm run pack:check
```

Regenerate the bundled sound:

```sh
python3 scripts/make_sound.py assets/soft-chime.wav
```
