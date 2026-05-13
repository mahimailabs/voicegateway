DEFAULT_DASHBOARD_URL = "http://127.0.0.1:9090"
DEFAULT_DASHBOARD_PORT = 9090

STATUS_RENDER = {
    "ok": "[green]PASS[/green]",
    "fail": "[red]FAIL[/red]",
    "skip": "[yellow]SKIP[/yellow]",
}

KNOWN_PROVIDERS = (
    "openai",
    "deepgram",
    "anthropic",
    "groq",
    "cartesia",
    "elevenlabs",
    "assemblyai",
)

SMOKE_MODALITIES: tuple[tuple[str, str], ...] = (
    ("stt", "STT"),
    ("llm", "LLM"),
    ("tts", "TTS"),
)

SMOKE_TEST_TIMEOUT_S = 10.0
VALIDATION_TIMEOUT_S = 5.0
