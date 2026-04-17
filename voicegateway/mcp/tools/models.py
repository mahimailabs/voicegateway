"""Model registration tools — list/register/delete."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway.mcp.errors import (
    ConfirmationRequiredError,
    ModelAlreadyExistsError,
    ModelNotFoundError,
    ProviderNotFoundError,
    ReadOnlyResourceError,
    ValidationError,
)
from voicegateway.mcp.schemas import (
    DeleteModelInput,
    ListModelsInput,
    RegisterModelInput,
)
from voicegateway.mcp.tools.base import ToolDef, make_tool

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway


def _parse(model_cls: type, arguments: dict[str, Any]) -> Any:
    try:
        return model_cls(**arguments)
    except Exception as exc:
        raise ValidationError(str(exc)) from exc


async def _gather_models(gateway: Gateway) -> list[dict[str, Any]]:
    """Combine YAML-defined and GUI-managed models."""
    out: list[dict[str, Any]] = []

    for modality, modality_models in gateway.config.models.items():
        if not isinstance(modality_models, dict):
            continue
        for model_id, mcfg in modality_models.items():
            if not isinstance(mcfg, dict):
                continue
            out.append({
                "model_id": model_id,
                "modality": modality,
                "provider_id": mcfg.get("provider", ""),
                "model_name": mcfg.get("model", ""),
                "default_voice": mcfg.get("default_voice"),
                "source": "yaml",
                "enabled": True,
            })

    if gateway.storage is not None:
        for row in await gateway.storage.list_managed_models():
            out.append({
                "model_id": row["model_id"],
                "modality": row["modality"],
                "provider_id": row["provider_id"],
                "model_name": row["model_name"],
                "display_name": row.get("display_name"),
                "default_language": row.get("default_language"),
                "default_voice": row.get("default_voice"),
                "source": "db",
                "enabled": row.get("enabled", True),
            })

    return out


def _provider_exists(gateway: Gateway, provider_id: str, managed: list[dict[str, Any]] | None = None) -> bool:
    if provider_id in gateway.config.providers:
        return True
    # managed is the list cached from gateway.storage.list_managed_providers()
    if managed is not None:
        return any(p["provider_id"] == provider_id for p in managed)
    return False


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------

LIST_MODELS_DOC = """List every registered model on the gateway.

Use this to answer "What models can I route to?" or before calling
``create_project`` to pick valid stt/llm/tts models. Each entry includes
whether it came from voicegw.yaml (source "yaml", read-only) or was added
via ``register_model`` (source "db", deletable).

Args:
    modality: Optional filter: "stt", "llm", or "tts".
    provider_id: Optional provider filter (e.g. "openai").
    enabled_only: If True (default), exclude disabled models.

Returns:
    A list of dicts: {model_id, modality, provider_id, model_name,
    default_voice, source ("yaml" | "db"), enabled}.
"""


async def _handle_list_models(gateway: Gateway, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = _parse(ListModelsInput, arguments)
    models = await _gather_models(gateway)

    if payload.modality:
        models = [m for m in models if m["modality"] == payload.modality]
    if payload.provider_id:
        models = [m for m in models if m["provider_id"] == payload.provider_id]
    if payload.enabled_only:
        models = [m for m in models if m.get("enabled", True)]

    return {"models": models, "count": len(models)}


# ---------------------------------------------------------------------------
# register_model
# ---------------------------------------------------------------------------

REGISTER_MODEL_DOC = """Register a new model (e.g. deepgram/nova-3).

Use this after ``add_provider`` when the user wants to expose a specific
model from that provider. The generated model id is "{provider_id}/{model_name}".
The provider must already exist (either in YAML or added via add_provider).

Args:
    modality: "stt", "llm", or "tts".
    provider_id: Must match an existing provider.
    model_name: The provider-specific model name (e.g. "nova-3", "gpt-4o-mini").
    display_name: Optional human-readable name for dashboards.
    default_language: Optional default language code (STT only).
    default_voice: Optional default voice id (TTS only).
    config: Optional dict of extra provider-specific options.

Returns:
    The new model record.

Raises:
    PROVIDER_NOT_FOUND if the provider doesn't exist yet.
    MODEL_ALREADY_EXISTS if the id is already registered.
"""


async def _handle_register_model(gateway: Gateway, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = _parse(RegisterModelInput, arguments)

    if gateway.storage is None:
        raise ValidationError(
            "Registering models requires cost_tracking.enabled=true.",
            details={"hint": "enable cost_tracking in voicegw.yaml"},
        )

    managed_providers = await gateway.storage.list_managed_providers()
    if not _provider_exists(gateway, payload.provider_id, managed_providers):
        raise ProviderNotFoundError(
            f"Provider '{payload.provider_id}' is not configured. "
            "Call add_provider first.",
            details={"provider_id": payload.provider_id},
        )

    model_id = f"{payload.provider_id}/{payload.model_name}"

    # Reject if already in YAML.
    yaml_modality = gateway.config.models.get(payload.modality) or {}
    if model_id in yaml_modality:
        raise ModelAlreadyExistsError(
            f"Model '{model_id}' is already defined in voicegw.yaml.",
            details={"model_id": model_id, "source": "yaml"},
        )

    # Reject if already in managed table.
    existing = await gateway.storage.get_managed_model(model_id)
    if existing is not None:
        raise ModelAlreadyExistsError(
            f"Model '{model_id}' is already registered. Use update_model (not "
            "yet implemented) or delete first.",
            details={"model_id": model_id, "source": "db"},
        )

    await gateway.storage.upsert_managed_model(
        model_id=model_id,
        modality=payload.modality,
        provider_id=payload.provider_id,
        model_name=payload.model_name,
        display_name=payload.display_name,
        default_language=payload.default_language,
        default_voice=payload.default_voice,
        extra_config=payload.config,
        enabled=True,
    )

    # Mirror into the running config so routing works immediately.
    modality_bucket = gateway.config.models.setdefault(payload.modality, {})
    modality_bucket[model_id] = {
        "provider": payload.provider_id,
        "model": payload.model_name,
        **({"default_voice": payload.default_voice} if payload.default_voice else {}),
        **({"default_language": payload.default_language} if payload.default_language else {}),
    }

    return {
        "model_id": model_id,
        "modality": payload.modality,
        "provider_id": payload.provider_id,
        "model_name": payload.model_name,
        "display_name": payload.display_name,
        "default_voice": payload.default_voice,
        "default_language": payload.default_language,
        "source": "db",
        "created": True,
    }


# ---------------------------------------------------------------------------
# delete_model
# ---------------------------------------------------------------------------

DELETE_MODEL_DOC = """Delete a GUI-registered model. Requires confirm=True.

DESTRUCTIVE. Returns a preview showing which projects reference the model
when confirm=False. Only models added via ``register_model`` can be deleted;
YAML models must be removed from the YAML file.

Args:
    model_id: The model id (e.g. "openai/gpt-4o-mini").
    confirm: Must be True to perform the delete.

Returns:
    Preview: {action: "would_delete", model_id, projects_affected}.
    Confirmed: {action: "deleted", model_id}.

Raises:
    MODEL_NOT_FOUND if no managed model has that id.
    READ_ONLY_RESOURCE if the model is defined in YAML.
    CONFIRMATION_REQUIRED if called without confirm=True.
"""


async def _handle_delete_model(gateway: Gateway, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = _parse(DeleteModelInput, arguments)

    # YAML check: find the model in any modality.
    in_yaml = False
    for modality_models in gateway.config.models.values():
        if not isinstance(modality_models, dict):
            continue
        if payload.model_id in modality_models:
            # If the model is also in managed table, prefer the managed delete.
            if gateway.storage is not None:
                managed = await gateway.storage.get_managed_model(payload.model_id)
                if managed is None:
                    in_yaml = True
                    break
            else:
                in_yaml = True
                break

    if in_yaml:
        raise ReadOnlyResourceError(
            f"Model '{payload.model_id}' is defined in voicegw.yaml and cannot "
            "be deleted via MCP.",
            details={"model_id": payload.model_id},
        )

    if gateway.storage is None:
        raise ModelNotFoundError(
            f"No managed model '{payload.model_id}' (storage disabled).",
            details={"model_id": payload.model_id},
        )

    managed = await gateway.storage.get_managed_model(payload.model_id)
    if managed is None:
        raise ModelNotFoundError(
            f"No managed model '{payload.model_id}'.",
            details={"model_id": payload.model_id},
        )

    # Find projects that reference this model.
    projects_affected: list[str] = []
    for pid, pcfg in gateway.config.projects.items():
        if pcfg.default_stack and pcfg.default_stack in gateway.config.stacks:
            stack = gateway.config.stacks[pcfg.default_stack]
            if payload.model_id in stack.values():
                projects_affected.append(pid)

    if not payload.confirm:
        raise ConfirmationRequiredError(
            f"Deleting model '{payload.model_id}' will impact "
            f"{len(projects_affected)} project(s). Call again with confirm=True.",
            details={
                "model_id": payload.model_id,
                "projects_affected": projects_affected,
            },
        )

    await gateway.storage.delete_managed_model(payload.model_id)

    # Remove from the running config.
    for modality_models in gateway.config.models.values():
        if isinstance(modality_models, dict):
            modality_models.pop(payload.model_id, None)

    return {
        "action": "deleted",
        "model_id": payload.model_id,
        "projects_affected": projects_affected,
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

MODEL_TOOLS: list[ToolDef] = [
    make_tool("list_models", LIST_MODELS_DOC, ListModelsInput, _handle_list_models),
    make_tool("register_model", REGISTER_MODEL_DOC, RegisterModelInput, _handle_register_model),
    make_tool("delete_model", DELETE_MODEL_DOC, DeleteModelInput, _handle_delete_model),
]
