---
title: voicegw init
description: Scaffold a voicegw.yaml configuration file from the bundled template.
---

# voicegw init

Create a `voicegw.yaml` configuration file from the bundled template.

## Purpose

`voicegw init` scaffolds a new configuration file with example
provider, model, and project definitions. Use it when you want a
hand-edited starting point. For a guided wizard that also installs
the daemon, use [`voicegw onboard`](/cli/onboard) instead.

The starter template ships inside the wheel at
`voicegateway/data/voicegw.example.yaml` and is copied verbatim to
the output path.

## Syntax

```bash
voicegw init [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--output` | `-o` | `string` | `./voicegw.yaml` | Output path for the generated config file. |

## Behaviour

1. If the target file already exists, the CLI prompts for
   confirmation before overwriting.
2. The starter template at `voicegateway/data/voicegw.example.yaml`
   (inside the installed wheel, resolved via `importlib.resources`)
   is written to the output path.

## Examples

### Create config in the current directory

```bash
voicegw init
```

Creates `./voicegw.yaml` with the example template.

### Create config at a custom path

```bash
voicegw init --output /etc/voicegateway/voicegw.yaml
```

### Create config with the short flag

```bash
voicegw init -o ~/projects/my-agent/voicegw.yaml
```

### Overwrite an existing config

```bash
voicegw init --output ./voicegw.yaml
# Prompts: "./voicegw.yaml already exists. Overwrite? [y/N]"
```

## Next steps

After running `init`:

1. Open the generated file in your editor and add your API keys.
2. Configure models under the `models:` section.
3. Verify with `voicegw status`.

## Related commands

- [`voicegw onboard`](/cli/onboard): the wizard alternative
  (also installs the daemon).
- [`voicegw status`](/cli/status): verify the config loads
  correctly.
- [`voicegw serve`](/cli/serve): run the daemon with the
  config.
- [`voicegw dashboard`](/cli/dashboard): open the dashboard.
