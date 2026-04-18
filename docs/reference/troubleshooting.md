# Troubleshooting

Common issues and their solutions. If your problem is not listed here, [open an issue](https://github.com/mahimailabs/voicegateway/issues) or check the [FAQ](/reference/faq).

## "No voicegw.yaml found"

**Error:** `ConfigError: No voicegw.yaml found`

**Cause:** VoiceGateway cannot find a configuration file.

**Fix:** VoiceGateway searches for config in this order:

1. `./voicegw.yaml` (current working directory)
2. `~/.config/voicegateway/voicegw.yaml`
3. `/etc/voicegateway/voicegw.yaml`

Generate a starter config:

```bash
voicegw init
```

Or set an explicit path:

```bash
export VOICEGW_CONFIG=/path/to/voicegw.yaml
voicegw serve
```

---

## "Provider not configured"

**Error:** `ValueError: Unknown provider 'xyz'. Available: anthropic, assemblyai, cartesia, deepgram, elevenlabs, groq, kokoro, ollama, openai, piper, whisper`

**Cause:** The provider name in your model ID does not match any registered provider, or the provider is not listed in your `voicegw.yaml`.

**Fix:**

1. Check spelling. Provider names are lowercase: `openai`, `deepgram`, `anthropic`, etc.
2. Ensure the provider is in your config file:
   ```yaml
   providers:
     openai:
       api_key: ${OPENAI_API_KEY}
   ```
3. If using a new provider, install the extra: `pip install voicegateway[openai]`

---

## "Budget exceeded"

**Error:** `BudgetExceededError: Project 'my-app' has exceeded its daily budget of $10.00`

**Cause:** The project's daily spending has hit the configured `daily_budget` and `budget_action` is set to `block`.

**Fix:**

- **Increase the budget** in `voicegw.yaml`:
  ```yaml
  projects:
    my-app:
      daily_budget: 50.00
  ```
- **Switch to warn mode** to log warnings instead of blocking:
  ```yaml
  projects:
    my-app:
      budget_action: warn
  ```
- **Check the dashboard** at `http://localhost:9090` to see where costs are accumulating
- Budgets reset daily at midnight UTC

---

## "Connection refused localhost:11434"

**Error:** `ConnectionRefusedError: [Errno 111] Connection refused` when using Ollama

**Cause:** Ollama is not running or is listening on a different address.

**Fix:**

1. Start Ollama:
   ```bash
   ollama serve
   ```
2. Verify it is running:
   ```bash
   curl http://localhost:11434/api/tags
   ```
3. If Ollama is on a different host or port, update your config:
   ```yaml
   providers:
     ollama:
       base_url: http://your-host:11434
   ```
4. If using Docker Compose with the `local` profile:
   ```bash
   docker compose --profile local up -d
   ```
   The Ollama container needs time to download models on first start.

---

## "Failed to decrypt" / Cryptography errors

**Error:** `cryptography.fernet.InvalidToken` or `Failed to decrypt API key`

**Cause:** The encryption key has changed or the stored encrypted value is corrupted.

**Fix:**

1. VoiceGateway uses the `cryptography` package for key encryption. If you rotated or lost the encryption key, re-set your API keys in `voicegw.yaml` using plain `${ENV_VAR}` references
2. Ensure `cryptography>=43.0` is installed:
   ```bash
   pip install --upgrade cryptography
   ```
3. If using encrypted storage, check that the `VOICEGW_ENCRYPTION_KEY` environment variable is set to the same value used when keys were stored

---

## Docker dashboard crashes on startup

**Error:** Dashboard container exits immediately or returns 502.

**Cause:** Usually a port conflict, missing config mount, or the frontend build is missing.

**Fix:**

1. Check logs:
   ```bash
   docker compose logs dashboard
   ```
2. Ensure the config file is mounted in your `docker-compose.yml`:
   ```yaml
   services:
     voicegateway:
       volumes:
         - ./voicegw.yaml:/app/voicegw.yaml:ro
   ```
   Then restart:
   ```bash
   docker compose up -d
   ```
3. Check port availability (default: 9090):
   ```bash
   lsof -i :9090
   ```
4. Rebuild the frontend if running from source:
   ```bash
   cd dashboard/frontend && npm run build
   ```
5. Ensure all dashboard dependencies are installed:
   ```bash
   pip install voicegateway[dashboard]
   ```

---

## "MCP tool not found"

**Error:** Coding agent reports the tool is not available or returns an empty tool list.

**Cause:** The MCP server is not running, the transport configuration is wrong, or the agent is not connected.

**Fix:**

1. Verify the MCP server is running:
   ```bash
   voicegw mcp --transport stdio
   ```
2. For HTTP/SSE transport, check the endpoint:
   ```bash
   curl http://localhost:8090/sse
   ```
3. Check your agent's MCP configuration. For Claude Code, add to `~/.claude/config.json`:
   ```json
   {
     "mcpServers": {
       "voicegateway": {
         "command": "voicegw",
         "args": ["mcp", "--transport", "stdio"]
       }
     }
   }
   ```
4. If using HTTP transport with auth, ensure `VOICEGW_MCP_TOKEN` matches between server and client
5. Restart the MCP server and the coding agent

---

## asyncio event loop errors

**Error:** `RuntimeError: There is no current event loop in thread 'MainThread'` or `RuntimeError: This event loop is already running`

**Cause:** Mixing synchronous and asynchronous code, or running in an environment that manages its own event loop (Jupyter, some web frameworks).

**Fix:**

1. Gateway model factory methods (`stt()`, `llm()`, `tts()`) are **synchronous** and handle event loop bridging internally via `_run_async()`. However, this can conflict if called inside an already-running event loop (e.g., Jupyter, web frameworks):
   ```python
   # This works in a normal script
   stt = gw.stt("deepgram/nova-3", project="my-app")

   # In an async context, the Gateway handles it — but if you get
   # loop errors, wrap your setup in a separate thread or use
   # nest_asyncio (see below).
   ```
2. If running in a script (not an async framework), use `asyncio.run()`:
   ```python
   import asyncio
   asyncio.run(main())
   ```
3. In Jupyter notebooks, use `nest_asyncio`:
   ```python
   import nest_asyncio
   nest_asyncio.apply()
   ```
4. If running tests, ensure `asyncio_mode = "auto"` is set in `pyproject.toml`

---

## "livekit plugin missing" / ModuleNotFoundError

**Error:** `ModuleNotFoundError: No module named 'livekit.plugins.deepgram'`

**Cause:** The provider's LiveKit plugin SDK is not installed.

**Fix:**

Install the specific provider extra:

```bash
pip install voicegateway[deepgram]
```

Or install all cloud providers:

```bash
pip install voicegateway[cloud]
```

Available extras: `openai`, `deepgram`, `anthropic`, `groq`, `cartesia`, `elevenlabs`, `assemblyai`, `whisper`, `kokoro`, `piper`.

Check what is installed:

```bash
pip list | grep livekit
```

---

## "Configuration validation failed"

**Error:** `ConfigError: Configuration validation failed: ...`

**Cause:** The `voicegw.yaml` file has structural errors, missing required fields, or invalid values.

**Fix:**

1. **Check YAML syntax:** use a YAML linter or `python -c "import yaml; yaml.safe_load(open('voicegw.yaml'))"`
2. **Required sections:** `providers`, `models` (with `stt`, `llm`, `tts` sub-keys)
3. **Environment variable references:** ensure `${VAR_NAME}` variables are actually set:
   ```bash
   echo $OPENAI_API_KEY  # Should not be empty
   ```
4. **Compare against the example config:**
   ```bash
   voicegw init --diff
   ```
5. **Common mistakes:**
   - Using tabs instead of spaces (YAML requires spaces)
   - Missing colon after a key
   - Incorrect indentation level
   - Referencing a provider in `models` that is not defined in `providers`

---

## Rate limiting errors

**Error:** `RateLimitError: Provider 'openai' rate limit exceeded` or HTTP 429 from the provider.

**Cause:** Too many requests to a provider in a short time.

**Fix:**

1. Configure rate limits in `voicegw.yaml` to stay under provider quotas:
   ```yaml
   rate_limiting:
     openai:
       requests_per_minute: 60
   ```
2. Add fallback providers so requests can be routed elsewhere:
   ```yaml
   fallbacks:
     llm:
       - anthropic/claude-3.5-sonnet
       - groq/llama-3.1-70b
   ```
3. Check your provider dashboard for current usage and limits

---

## Database locked errors

**Error:** `sqlite3.OperationalError: database is locked`

**Cause:** Multiple processes writing to the same SQLite database file.

**Fix:**

1. Ensure only one VoiceGateway server instance writes to the database
2. If running multiple instances, give each its own `db_path`:
   ```yaml
   cost_tracking:
     db_path: /data/voicegw-instance-1.db
   ```
3. Or use the `VOICEGW_DB_PATH` environment variable:
   ```bash
   VOICEGW_DB_PATH=/data/voicegw-1.db voicegw serve --port 8080
   ```
4. The dashboard reads the database (read-only) and should not cause locks

## Related pages

- [FAQ](/reference/faq)
- [Installation](/guide/installation)
- [Quick Start](/guide/quick-start)
- [Contributing](/contributing/)
- [Changelog](/reference/changelog)
