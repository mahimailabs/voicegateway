# voicegw init

Create a `voicegw.yaml` configuration file from the bundled template.

## Purpose

The `init` command scaffolds a new configuration file with example provider, model, and project definitions. This is the recommended first step when setting up VoiceGateway. If an example config ships with the installed package, it is copied directly; otherwise, a minimal skeleton is generated.

## Syntax

```bash
voicegw init [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--output` | `-o` | `string` | `./voicegw.yaml` | Output path for the generated config file. |

## Behavior

1. If the target file already exists, the CLI prompts for confirmation before overwriting.
2. If the package includes `voicegw.example.yaml` (or the legacy `gateway.example.yaml`), that file is copied to the output path.
3. If no example config is found, a minimal YAML skeleton is written with empty `providers`, `models`, and `projects` sections.

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

### Create config with short flag

```bash
voicegw init -o ~/projects/my-agent/voicegw.yaml
```

### Overwrite an existing config

```bash
voicegw init --output ./voicegw.yaml
# Prompts: "./voicegw.yaml already exists. Overwrite? [y/N]"
```

## Next Steps

After running `init`:

1. Open the generated file in your editor and add your API keys.
2. Configure models under the `models:` section.
3. Verify with `voicegw status`.

## Related Commands

- [`voicegw status`](/cli/status) -- verify the config loads correctly
- [`voicegw serve`](/cli/serve) -- start the HTTP API with the config
- [`voicegw dashboard`](/cli/dashboard) -- start the web dashboard
