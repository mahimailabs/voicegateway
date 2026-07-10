---
title: voicegw init
description: Scaffold a voicegw.yaml configuration file from the bundled template.
---

# voicegw init

Create a `voicegw.yaml` configuration file from the bundled template.

## Synopsis

Scaffold a new config file with example provider, model, and project definitions. Use it when you want a hand-edited starting point. For a guided wizard that also installs the daemon, use [`voicegw onboard`](/cli/onboard) instead.

The starter template ships inside the wheel at `voicegateway/data/voicegw.example.yaml` and is copied verbatim to the output path.

## Usage

```bash
voicegw init [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--output` | `-o` | `string` | `./voicegw.yaml` | Output path for the generated config file. |

## Behaviour

1. If the target file already exists, the CLI prompts for confirmation before overwriting.
2. The starter template at `voicegateway/data/voicegw.example.yaml` (inside the installed wheel, resolved via `importlib.resources`) is written to the output path.

## Examples

```bash
# Create config in the current directory
voicegw init
```

```bash
# Create config at a custom path
voicegw init --output /etc/voicegateway/voicegw.yaml
```

```bash
# Create config with the short flag
voicegw init -o ~/projects/my-agent/voicegw.yaml
```

```bash
# Overwrite an existing config (prompts for confirmation)
voicegw init --output ./voicegw.yaml
# Prompts: "./voicegw.yaml already exists. Overwrite? [y/N]"
```

## After running init

1. Open the generated file in your editor and add your API keys.
2. Configure models under the `models:` section.
3. Verify the config with `voicegw status`.

## Related

[`voicegw onboard`](/cli/onboard) | [`voicegw status`](/cli/status) | [`voicegw serve`](/cli/serve) | [Configuration reference](/configuration/voicegw-yaml)
