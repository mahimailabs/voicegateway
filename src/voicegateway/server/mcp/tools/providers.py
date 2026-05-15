"""Provider management tools — list/get/test/add/delete."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from voicegateway.core import registry as _registry
from voicegateway.core.crypto import decrypt, mask
from voicegateway.schemas.mcp.providers_schema import (
    AddProviderInput,
    DeleteProviderInput,
    GetProviderInput,
    ListProvidersInput,
    TestProviderInput,
    VgAddProviderInput,
    VgListProvidersInput,
    VgRemoveProviderInput,
    VgSetProviderKeyInput,
    VgTestProviderKeyInput,
)
from voicegateway.server.mcp.errors import (
    ConfirmationRequiredError,
    ProviderAlreadyExistsError,
    ProviderNotFoundError,
    ProviderTestFailedError,
    ReadOnlyResourceError,
    ValidationError,
)
from voicegateway.server.mcp.tools.base import ToolDef, make_tool

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway


def _parse(model_cls: type, arguments: dict[str, Any]) -> Any:
    try:
        return model_cls(**arguments)
    except Exception as exc:
        raise ValidationError(str(exc)) from exc


def _mask_api_key(key: str | None) -> str | None:
    if not key:
        return None
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


async def _gather_providers(gateway: Gateway) -> list[dict[str, Any]]:
    """Combine YAML-defined and GUI-managed providers into one list."""
    cfg = gateway.config
    local_names = {"ollama", "whisper", "kokoro", "piper"}
    out: list[dict[str, Any]] = []

    for name, provider_cfg in cfg.providers.items():
        api_key = (
            provider_cfg.get("api_key") if isinstance(provider_cfg, dict) else None
        )
        out.append(
            {
                "provider_id": name,
                "provider_type": name,
                "source": "yaml",
                "enabled": True,
                "api_key_masked": _mask_api_key(api_key),
                "base_url": provider_cfg.get("base_url")
                if isinstance(provider_cfg, dict)
                else None,
                "type": "local" if name in local_names else "cloud",
            }
        )

    if gateway.storage is not None:
        import logging

        _log = logging.getLogger(__name__)
        for row in await gateway.storage.list_managed_providers():
            try:
                plaintext_key = decrypt(row.get("api_key_encrypted", ""))
            except ValueError:
                _log.warning(
                    "Failed to decrypt key for provider '%s'", row["provider_id"]
                )
                plaintext_key = ""
            out.append(
                {
                    "provider_id": row["provider_id"],
                    "provider_type": row["provider_type"],
                    "source": "db",
                    "enabled": bool(plaintext_key),
                    "api_key_masked": mask(plaintext_key) if plaintext_key else None,
                    "base_url": row.get("base_url"),
                    "type": "local" if row["provider_type"] in local_names else "cloud",
                }
            )

    return out


LIST_PROVIDERS_DOC = """List every provider configured on this gateway.

Use this to answer "What providers do I have?" or as the first step when
the agent needs to decide whether to use ``add_provider`` or reference an
existing one. Includes both providers defined in voicegw.yaml (source
"yaml") and providers added via this tool (source "db").

Args:
    (none)

Returns:
    A list of dicts, each: {provider_id, provider_type, source ("yaml" |
    "db"), enabled, api_key_masked, base_url, type ("cloud" | "local")}.
"""


async def _handle_list_providers(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    _parse(ListProvidersInput, arguments)
    providers = await _gather_providers(gateway)
    return {"providers": providers, "count": len(providers)}


GET_PROVIDER_DOC = """Return full details for one provider, with the API key masked.

Use this after ``list_providers`` when the user wants details on a specific
provider (e.g. the base URL, how many models depend on it). The API key is
never returned in full — only as a masked preview like "sk-a...1f2b".

Args:
    provider_id: The id of the provider to fetch.

Returns:
    A dict: {provider_id, provider_type, source, api_key_masked, base_url,
    type, model_count}.

Raises:
    PROVIDER_NOT_FOUND if no provider has that id.
"""


async def _handle_get_provider(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(GetProviderInput, arguments)
    providers = await _gather_providers(gateway)
    for p in providers:
        if p["provider_id"] == payload.provider_id:
            # enrich with model_count
            model_count = 0
            for modality_models in gateway.config.models.values():
                if not isinstance(modality_models, dict):
                    continue
                for mcfg in modality_models.values():
                    if (
                        isinstance(mcfg, dict)
                        and mcfg.get("provider") == payload.provider_id
                    ):
                        model_count += 1
            result = dict(p)
            result["model_count"] = model_count
            return result
    raise ProviderNotFoundError(
        f"No provider with id '{payload.provider_id}'. "
        f"Use list_providers to see what's configured.",
        details={"provider_id": payload.provider_id},
    )


TEST_PROVIDER_DOC = """Test connectivity to a provider.

Use this after ``add_provider`` or when troubleshooting to verify the gateway
can reach the provider's API with the configured credentials. Calls the
provider's ``health_check`` method, which makes a minimal live request.

Args:
    provider_id: The id of the provider to test.

Returns:
    {status: "ok" | "failed", latency_ms: int, message: str}. On success,
    latency_ms is the wall-clock time of the health check; on failure the
    message explains what went wrong.

Raises:
    PROVIDER_NOT_FOUND if no provider has that id.
"""


async def _handle_test_provider(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(TestProviderInput, arguments)

    cfg = gateway.config
    provider_cfg: dict[str, Any] | None = None
    provider_type = payload.provider_id
    if payload.provider_id in cfg.providers:
        provider_cfg = cfg.providers[payload.provider_id]
    elif gateway.storage is not None:
        row = await gateway.storage.get_managed_provider(payload.provider_id)
        if row is not None:
            provider_type = row["provider_type"]
            provider_cfg = {
                "api_key": decrypt(row.get("api_key_encrypted", "")),
                "base_url": row.get("base_url"),
                **(row.get("extra_config") or {}),
            }

    if provider_cfg is None:
        raise ProviderNotFoundError(
            f"No provider '{payload.provider_id}'. Add it first via add_provider.",
            details={"provider_id": payload.provider_id},
        )

    if provider_type not in _registry._PROVIDER_REGISTRY:
        return {
            "status": "failed",
            "latency_ms": 0,
            "message": f"Unknown provider type '{provider_type}'.",
        }

    try:
        provider = _registry.create_provider(provider_type, provider_cfg)
    except ImportError as exc:
        return {"status": "failed", "latency_ms": 0, "message": str(exc)}

    start = time.perf_counter()
    try:
        ok = await provider.health_check()
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "latency_ms": int((time.perf_counter() - start) * 1000),
            "message": f"{type(exc).__name__}: {exc}",
        }
    latency_ms = int((time.perf_counter() - start) * 1000)
    return {
        "status": "ok" if ok else "failed",
        "latency_ms": latency_ms,
        "message": "reachable" if ok else "provider returned unhealthy",
    }


ADD_PROVIDER_DOC = """Register a new voice AI provider or replace an existing GUI-added one.

Use this to add Deepgram, OpenAI, Cartesia, etc. so the gateway can route
requests through them. Local providers (ollama, whisper, kokoro, piper)
use an empty api_key. After adding a provider, call ``register_model`` to
add specific models from it (e.g. deepgram/nova-3).

Args:
    provider_id: Unique identifier, typically the provider name in lowercase.
    provider_type: One of the supported types: deepgram, openai, anthropic,
        groq, cartesia, elevenlabs, assemblyai, ollama, whisper, kokoro, piper.
    api_key: API key from the provider console. Empty string for local
        providers that don't need one.
    base_url: Optional custom base URL (e.g. self-hosted Ollama).

Returns:
    The created provider record with api_key masked.

Raises:
    PROVIDER_ALREADY_EXISTS if the id collides with a YAML provider.
    VALIDATION_ERROR if the provider_type is unknown.
"""


async def _handle_add_provider(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(AddProviderInput, arguments)

    if payload.provider_type not in _registry._PROVIDER_REGISTRY:
        raise ValidationError(
            f"Unknown provider_type '{payload.provider_type}'. "
            f"Supported: {', '.join(sorted(_registry._PROVIDER_REGISTRY))}.",
            details={"supported": sorted(_registry._PROVIDER_REGISTRY)},
        )

    if payload.provider_id in gateway.config.providers:
        raise ProviderAlreadyExistsError(
            f"Provider '{payload.provider_id}' is defined in voicegw.yaml and "
            "cannot be replaced via MCP. Edit the YAML or choose a different id.",
            details={"provider_id": payload.provider_id, "source": "yaml"},
        )

    if gateway.storage is None:
        raise ValidationError(
            "Managed providers require cost_tracking.enabled=true (SQLite storage).",
            details={"hint": "enable cost_tracking in voicegw.yaml"},
        )

    test_cfg = {"api_key": payload.api_key, "base_url": payload.base_url}
    local = payload.provider_type in {"ollama", "whisper", "kokoro", "piper"}
    if not local:
        try:
            provider_instance = _registry.create_provider(
                payload.provider_type, test_cfg
            )
            ok = await provider_instance.health_check()
            if not ok:
                raise ProviderTestFailedError(
                    f"Credentials for '{payload.provider_id}' failed health check. "
                    "Not saved.",
                    details={"provider_id": payload.provider_id},
                )
        except ProviderTestFailedError:
            raise
        except ImportError as exc:
            raise ProviderTestFailedError(
                f"Plugin not installed for '{payload.provider_type}': {exc}",
                details={"provider_type": payload.provider_type},
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderTestFailedError(
                f"Provider test raised {type(exc).__name__}: {exc}. Not saved.",
                details={"provider_id": payload.provider_id},
            ) from exc

    await gateway.storage.upsert_managed_provider(
        provider_id=payload.provider_id,
        provider_type=payload.provider_type,
        api_key=payload.api_key,
        base_url=payload.base_url,
    )
    await gateway.refresh_config()

    return {
        "provider_id": payload.provider_id,
        "provider_type": payload.provider_type,
        "api_key_masked": _mask_api_key(payload.api_key),
        "base_url": payload.base_url,
        "source": "db",
        "created": True,
    }


DELETE_PROVIDER_DOC = """Delete a GUI-added provider. Requires confirm=True.

DESTRUCTIVE. By default this returns a preview with the models and projects
that reference the provider; the agent MUST show the preview to the user,
get explicit confirmation, then call again with confirm=True to actually
delete. Cannot delete YAML-defined providers — disable them by editing YAML.

Args:
    provider_id: The id to delete.
    confirm: Must be True to perform the delete. Default False returns a preview.

Returns:
    Preview mode: {action: "would_delete", provider_id, models_affected,
    projects_affected}. Confirmed mode: {action: "deleted", provider_id}.

Raises:
    PROVIDER_NOT_FOUND if the id doesn't exist.
    READ_ONLY_RESOURCE if the provider is defined in YAML.
    CONFIRMATION_REQUIRED if called without confirm=True.
"""


async def _handle_delete_provider(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(DeleteProviderInput, arguments)

    is_in_config = payload.provider_id in gateway.config.providers
    is_managed = False
    if gateway.storage is not None:
        is_managed = (
            await gateway.storage.get_managed_provider(payload.provider_id) is not None
        )
    if is_in_config and not is_managed:
        raise ReadOnlyResourceError(
            f"Provider '{payload.provider_id}' is defined in voicegw.yaml and "
            "cannot be deleted via MCP. Remove it from YAML and restart.",
            details={"provider_id": payload.provider_id},
        )

    if gateway.storage is None:
        raise ProviderNotFoundError(
            f"No managed provider '{payload.provider_id}' (storage disabled).",
            details={"provider_id": payload.provider_id},
        )

    managed = await gateway.storage.get_managed_provider(payload.provider_id)
    if managed is None:
        raise ProviderNotFoundError(
            f"No managed provider '{payload.provider_id}' in storage.",
            details={"provider_id": payload.provider_id},
        )

    models_affected: list[str] = []
    for modality_models in gateway.config.models.values():
        if not isinstance(modality_models, dict):
            continue
        for model_id, mcfg in modality_models.items():
            if isinstance(mcfg, dict) and mcfg.get("provider") == payload.provider_id:
                models_affected.append(model_id)

    projects_affected: list[str] = []
    for pid, pcfg in gateway.config.projects.items():
        if pcfg.default_stack and pcfg.default_stack in gateway.config.stacks:
            stack = gateway.config.stacks[pcfg.default_stack]
            for _, model_ref in stack.items():
                if model_ref.startswith(f"{payload.provider_id}/"):
                    projects_affected.append(pid)
                    break

    if not payload.confirm:
        raise ConfirmationRequiredError(
            f"Deleting provider '{payload.provider_id}' will impact "
            f"{len(models_affected)} model(s) and {len(projects_affected)} project(s). "
            "Call again with confirm=True to proceed.",
            details={
                "provider_id": payload.provider_id,
                "models_affected": models_affected,
                "projects_affected": projects_affected,
            },
        )

    await gateway.storage.delete_managed_provider(payload.provider_id)
    await gateway.refresh_config()

    return {
        "action": "deleted",
        "provider_id": payload.provider_id,
        "models_affected": models_affected,
        "projects_affected": projects_affected,
    }


VG_ADD_PROVIDER_DOC = """Add or update a provider key scoped to a project.

The api_key is encrypted with Fernet before being persisted. The
managed_providers row's ``provider_id`` is built as ``"<project>:<provider>"``
so two projects can each carry their own openai key without colliding.

Args:
    project: Project name to scope this key to.
    provider: Canonical provider type (e.g. ``openai``, ``deepgram``).
    api_key: The provider API key.
    base_url: Optional override for the provider base URL.

Returns:
    {project, provider, provider_id, api_key_masked, source: "db", created: True}.

Raises:
    VALIDATION if provider is not in the supported set.
    PROVIDER_ALREADY_EXISTS if a YAML-defined provider already owns
    this provider_id (rare; the project-prefixed id usually avoids it).
"""


async def _handle_vg_add_provider(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(VgAddProviderInput, arguments)

    if payload.provider not in _registry._PROVIDER_REGISTRY:
        raise ValidationError(
            f"Unknown provider '{payload.provider}'. "
            f"Supported: {', '.join(sorted(_registry._PROVIDER_REGISTRY))}.",
            details={"supported": sorted(_registry._PROVIDER_REGISTRY)},
        )

    if gateway.storage is None:
        raise ValidationError(
            "Managed providers require cost_tracking.enabled=true (SQLite storage).",
            details={"hint": "enable cost_tracking in voicegw.yaml"},
        )

    composite_id = f"{payload.project}:{payload.provider}"

    existing_top_level = gateway.config.providers.get(composite_id)
    if existing_top_level is not None and existing_top_level.get("_source") != "db":
        raise ProviderAlreadyExistsError(
            f"Provider id '{composite_id}' is already defined in voicegw.yaml.",
            details={"provider_id": composite_id, "source": "yaml"},
        )
    project_cfg = gateway.config.projects.get(payload.project)
    if (
        project_cfg is not None
        and payload.provider in project_cfg.providers
        and project_cfg.providers[payload.provider].get("_source") != "db"
    ):
        raise ProviderAlreadyExistsError(
            f"Project '{payload.project}' already defines '{payload.provider}' "
            "in voicegw.yaml. Edit the YAML or remove that entry; the "
            "inference resolver reads YAML before DB-managed rows, so a DB "
            "write here would silently shadow nothing.",
            details={
                "provider_id": composite_id,
                "project": payload.project,
                "provider": payload.provider,
                "source": "yaml",
                "yaml_path": f"projects.{payload.project}.providers.{payload.provider}",
            },
        )

    await gateway.storage.upsert_managed_provider(
        provider_id=composite_id,
        provider_type=payload.provider,
        api_key=payload.api_key,
        base_url=payload.base_url,
        project=payload.project,
    )
    await gateway.refresh_config()

    return {
        "project": payload.project,
        "provider": payload.provider,
        "provider_id": composite_id,
        "api_key_masked": _mask_api_key(payload.api_key),
        "base_url": payload.base_url,
        "source": "db",
        "created": True,
    }


VG_REMOVE_PROVIDER_DOC = """Remove the per-project provider key written by vg_add_provider.

The composite ``"<project>:<provider>"`` row is deleted from the
managed_providers table. YAML-defined providers (``_source: yaml``)
cannot be removed via this tool — edit voicegw.yaml instead.

Args:
    project: Project name the key was scoped to.
    provider: Canonical provider type whose key was stored.

Returns:
    {project, provider, provider_id, deleted: True}.

Raises:
    PROVIDER_NOT_FOUND if no DB-managed row matches the composite id.
    READ_ONLY_RESOURCE if the matching row is YAML-defined.
"""


async def _handle_vg_remove_provider(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(VgRemoveProviderInput, arguments)
    composite_id = f"{payload.project}:{payload.provider}"

    existing = gateway.config.providers.get(composite_id)
    if existing is not None and existing.get("_source") != "db":
        raise ReadOnlyResourceError(
            f"Provider '{composite_id}' is defined in voicegw.yaml and "
            "cannot be removed via MCP. Edit voicegw.yaml instead.",
            details={"provider_id": composite_id, "source": "yaml"},
        )

    if gateway.storage is None:
        raise ProviderNotFoundError(
            f"No managed provider '{composite_id}' (storage disabled).",
            details={"provider_id": composite_id},
        )

    managed = await gateway.storage.get_managed_provider(composite_id)
    if managed is None:
        raise ProviderNotFoundError(
            f"No managed provider '{composite_id}' for project "
            f"'{payload.project}' / provider '{payload.provider}'.",
            details={
                "provider_id": composite_id,
                "project": payload.project,
                "provider": payload.provider,
            },
        )

    await gateway.storage.delete_managed_provider(composite_id)
    await gateway.refresh_config()

    return {
        "project": payload.project,
        "provider": payload.provider,
        "provider_id": composite_id,
        "deleted": True,
    }


VG_LIST_PROVIDERS_DOC = """List per-project provider keys with optional project filter.

The api_key field is masked (first/last 4 chars) — full keys never
leave storage. YAML-defined providers are returned alongside DB-managed
ones, tagged with ``source`` so callers can tell them apart.

Args:
    project: Optional project filter. When given, only rows scoped to
        this project are returned (DB-managed rows whose project column
        matches AND any YAML-defined entries under
        ``projects.<project>.providers``). When None, returns the full
        cross-project view.

Returns:
    {providers: [{project, provider, provider_id, source,
    api_key_masked, base_url, type}]}.
"""


def _yaml_per_project_entries(gateway: Gateway) -> list[dict[str, Any]]:
    """Flatten projects.<id>.providers blocks from voicegw.yaml into
    a list of provider dicts shaped like the DB output.
    """
    local_names = {"ollama", "whisper", "kokoro", "piper"}
    out: list[dict[str, Any]] = []
    for proj_id, proj_cfg in gateway.config.projects.items():
        for prov_name, prov_cfg in proj_cfg.providers.items():
            if not isinstance(prov_cfg, dict):
                continue
            if prov_cfg.get("_source") == "db":
                continue
            api_key = prov_cfg.get("api_key")
            out.append(
                {
                    "project": proj_id,
                    "provider": prov_name,
                    "provider_id": f"{proj_id}:{prov_name}",
                    "source": "yaml",
                    "api_key_masked": _mask_api_key(api_key),
                    "base_url": prov_cfg.get("base_url"),
                    "type": "local" if prov_name in local_names else "cloud",
                }
            )
    return out


async def _handle_vg_list_providers(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(VgListProvidersInput, arguments)
    local_names = {"ollama", "whisper", "kokoro", "piper"}
    rows: list[dict[str, Any]] = []

    rows.extend(_yaml_per_project_entries(gateway))

    yaml_ids = {r["provider_id"] for r in rows}
    if gateway.storage is not None:
        for row in await gateway.storage.list_managed_providers():
            pid = row["provider_id"]
            if pid in yaml_ids:
                continue
            project = row.get("project")
            if project is None:
                continue
            try:
                plaintext = decrypt(row.get("api_key_encrypted", ""))
            except ValueError:
                plaintext = ""
            provider_type = row["provider_type"]
            rows.append(
                {
                    "project": project,
                    "provider": provider_type,
                    "provider_id": pid,
                    "source": "db",
                    "api_key_masked": mask(plaintext) if plaintext else None,
                    "base_url": row.get("base_url"),
                    "type": "local" if provider_type in local_names else "cloud",
                }
            )

    if payload.project is not None:
        rows = [r for r in rows if r["project"] == payload.project]

    rows.sort(key=lambda r: (r["project"], r["provider"]))
    return {"providers": rows}


VG_SET_PROVIDER_KEY_DOC = """Rotate the per-project key written by vg_add_provider.

Differs from ``vg_add_provider`` in one way: the
``"<project>:<provider>"`` composite row must already exist. Rotating
a non-existent pair raises PROVIDER_NOT_FOUND so the caller does not
silently create a new row when they meant to rotate.

Args:
    project: Project name the key is scoped to.
    provider: Canonical provider type whose key is being rotated.
    api_key: New api key (Fernet-encrypted at rest).
    base_url: Optional base URL update.

Returns:
    {project, provider, provider_id, api_key_masked, base_url,
    source: "db", rotated: True}.

Raises:
    PROVIDER_NOT_FOUND if the (project, provider) pair has no
    matching DB row. Use vg_add_provider to create it first.
    READ_ONLY_RESOURCE if the matching row is YAML-defined.
"""


async def _handle_vg_set_provider_key(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(VgSetProviderKeyInput, arguments)

    if payload.provider not in _registry._PROVIDER_REGISTRY:
        raise ValidationError(
            f"Unknown provider '{payload.provider}'. "
            f"Supported: {', '.join(sorted(_registry._PROVIDER_REGISTRY))}.",
            details={"supported": sorted(_registry._PROVIDER_REGISTRY)},
        )

    composite_id = f"{payload.project}:{payload.provider}"

    existing = gateway.config.providers.get(composite_id)
    if existing is not None and existing.get("_source") != "db":
        raise ReadOnlyResourceError(
            f"Provider '{composite_id}' is defined in voicegw.yaml and "
            "cannot be rotated via MCP. Edit voicegw.yaml instead.",
            details={"provider_id": composite_id, "source": "yaml"},
        )

    if gateway.storage is None:
        raise ProviderNotFoundError(
            f"No managed provider '{composite_id}' (storage disabled).",
            details={"provider_id": composite_id},
        )

    managed = await gateway.storage.get_managed_provider(composite_id)
    if managed is None:
        raise ProviderNotFoundError(
            f"No managed provider '{composite_id}' to rotate. "
            "Use vg_add_provider to create it first.",
            details={
                "provider_id": composite_id,
                "project": payload.project,
                "provider": payload.provider,
            },
        )

    base_url = (
        payload.base_url if payload.base_url is not None else managed.get("base_url")
    )

    await gateway.storage.upsert_managed_provider(
        provider_id=composite_id,
        provider_type=payload.provider,
        api_key=payload.api_key,
        base_url=base_url,
        project=payload.project,
    )
    await gateway.refresh_config()

    return {
        "project": payload.project,
        "provider": payload.provider,
        "provider_id": composite_id,
        "api_key_masked": _mask_api_key(payload.api_key),
        "base_url": base_url,
        "source": "db",
        "rotated": True,
    }


VG_TEST_PROVIDER_KEY_DOC = """Verify a per-project provider key actually authenticates.

Resolves the ``"<project>:<provider>"`` key from voicegw.yaml's
``projects.<project>.providers.<provider>`` block first, then falls
back to the DB-managed row. Constructs the matching provider plugin
and runs its ``health_check()`` (a lightweight call like listing
models). Returns ``{status: "ok"}`` on success or
``{status: "failed", message: ...}`` on auth failure or transport
error.

Args:
    project: Project the key is scoped to.
    provider: Canonical provider type whose key is being tested.

Returns:
    {project, provider, provider_id, status, latency_ms, message}.

Raises:
    PROVIDER_NOT_FOUND if neither the YAML nor the DB layer has a
    matching key.
"""


async def _handle_vg_test_provider_key(
    gateway: Gateway, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = _parse(VgTestProviderKeyInput, arguments)
    composite_id = f"{payload.project}:{payload.provider}"

    provider_cfg: dict[str, Any] | None = None

    yaml_project = gateway.config.projects.get(payload.project)
    if yaml_project is not None and payload.provider in yaml_project.providers:
        provider_cfg = dict(yaml_project.providers[payload.provider])

    if provider_cfg is None and gateway.storage is not None:
        row = await gateway.storage.get_managed_provider(composite_id)
        if row is not None:
            provider_cfg = {
                "api_key": decrypt(row.get("api_key_encrypted", "")),
                "base_url": row.get("base_url"),
                **(row.get("extra_config") or {}),
            }

    if provider_cfg is None:
        raise ProviderNotFoundError(
            f"No managed provider for project '{payload.project}' / "
            f"provider '{payload.provider}'. Use vg_add_provider to add it.",
            details={
                "project": payload.project,
                "provider": payload.provider,
                "provider_id": composite_id,
            },
        )

    if payload.provider not in _registry._PROVIDER_REGISTRY:
        return {
            "project": payload.project,
            "provider": payload.provider,
            "provider_id": composite_id,
            "status": "failed",
            "latency_ms": 0,
            "message": f"Unknown provider type '{payload.provider}'.",
        }

    try:
        provider_instance = _registry.create_provider(payload.provider, provider_cfg)
    except ImportError as exc:
        return {
            "project": payload.project,
            "provider": payload.provider,
            "provider_id": composite_id,
            "status": "failed",
            "latency_ms": 0,
            "message": str(exc),
        }

    start = time.perf_counter()
    try:
        ok = await provider_instance.health_check()
    except Exception as exc:  # noqa: BLE001
        return {
            "project": payload.project,
            "provider": payload.provider,
            "provider_id": composite_id,
            "status": "failed",
            "latency_ms": int((time.perf_counter() - start) * 1000),
            "message": f"{type(exc).__name__}: {exc}",
        }
    latency_ms = int((time.perf_counter() - start) * 1000)
    return {
        "project": payload.project,
        "provider": payload.provider,
        "provider_id": composite_id,
        "status": "ok" if ok else "failed",
        "latency_ms": latency_ms,
        "message": "reachable" if ok else "provider returned unhealthy",
    }


PROVIDER_TOOLS: list[ToolDef] = [
    make_tool(
        "list_providers", LIST_PROVIDERS_DOC, ListProvidersInput, _handle_list_providers
    ),
    make_tool("get_provider", GET_PROVIDER_DOC, GetProviderInput, _handle_get_provider),
    make_tool(
        "test_provider", TEST_PROVIDER_DOC, TestProviderInput, _handle_test_provider
    ),
    make_tool("add_provider", ADD_PROVIDER_DOC, AddProviderInput, _handle_add_provider),
    make_tool(
        "delete_provider",
        DELETE_PROVIDER_DOC,
        DeleteProviderInput,
        _handle_delete_provider,
    ),
    make_tool(
        "vg_add_provider",
        VG_ADD_PROVIDER_DOC,
        VgAddProviderInput,
        _handle_vg_add_provider,
    ),
    make_tool(
        "vg_remove_provider",
        VG_REMOVE_PROVIDER_DOC,
        VgRemoveProviderInput,
        _handle_vg_remove_provider,
    ),
    make_tool(
        "vg_list_providers",
        VG_LIST_PROVIDERS_DOC,
        VgListProvidersInput,
        _handle_vg_list_providers,
    ),
    make_tool(
        "vg_set_provider_key",
        VG_SET_PROVIDER_KEY_DOC,
        VgSetProviderKeyInput,
        _handle_vg_set_provider_key,
    ),
    make_tool(
        "vg_test_provider_key",
        VG_TEST_PROVIDER_KEY_DOC,
        VgTestProviderKeyInput,
        _handle_vg_test_provider_key,
    ),
]
