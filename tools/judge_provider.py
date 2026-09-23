"""Probe the configured judge provider and fall back without exposing API keys."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROVIDERS = {"openai", "gemini"}
TIMEOUT_SECONDS = 15


class ProviderProbeError(RuntimeError):
    """A provider could not return a usable model list; never includes credentials."""

    def __init__(self, provider: str, reason: str):
        self.provider = provider
        self.reason = reason
        super().__init__(f"{provider}: {reason}")


class NoUsableProvider(RuntimeError):
    """Neither the primary nor the configured fallback passed its smoke check."""


@dataclass(frozen=True)
class JudgeSelection:
    provider: str
    model_id: str
    fallback_from: str | None = None
    attempts: tuple[str, ...] = ()


def _request_json(provider: str, api_key: str) -> dict:
    if provider == "openai":
        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    elif provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        headers = {"x-goog-api-key": api_key, "Accept": "application/json"}
    else:
        raise ProviderProbeError(provider, "unsupported provider")

    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise ProviderProbeError(provider, f"HTTP {error.code}") from None
    except (URLError, TimeoutError, OSError):
        raise ProviderProbeError(provider, "network error or timeout") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderProbeError(provider, "invalid JSON response") from None


def _model_ids(provider: str, payload: dict) -> list[str]:
    if provider == "openai":
        models = payload.get("data", [])
        return sorted(
            item["id"] for item in models
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        )

    models = payload.get("models", [])
    ids = []
    for item in models:
        if not isinstance(item, dict):
            continue
        actions = item.get("supportedGenerationMethods")
        if actions is None:
            actions = item.get("supportedActions")
        if not isinstance(actions, list) or "generateContent" not in actions:
            continue
        model_id = item.get("baseModelId")
        if not isinstance(model_id, str) or not model_id:
            model_id = item.get("name", "")
            if isinstance(model_id, str) and model_id.startswith("models/"):
                model_id = model_id.removeprefix("models/")
        if isinstance(model_id, str) and model_id:
            ids.append(model_id)
    return sorted(set(ids))


def _default_model_id(provider: str, model_ids: list[str]) -> str:
    if provider == "gemini":
        ranked = []
        for model_id in model_ids:
            match = re.fullmatch(r"gemini-(\d+(?:\.\d+)*?)-flash(?P<lite>-lite)?", model_id)
            if match:
                version = tuple(int(part) for part in match.group(1).split("."))
                # Prefer Flash-Lite for judge calls: it is the lower-cost,
                # higher-throughput option when both stable variants exist.
                lite_rank = 1 if match.group("lite") else 0
                ranked.append((lite_rank, version, model_id))
        if ranked:
            return max(ranked)[2]
    return model_ids[0]


def select_judge_provider(
    *,
    keys: dict[str, str],
    primary: str = "openai",
    fallback: str = "gemini",
    models: dict[str, str] | None = None,
    probe=_request_json,
) -> JudgeSelection:
    """Return the first usable provider, trying the fallback after primary failure."""
    if primary not in PROVIDERS:
        raise ValueError(f"unsupported primary provider: {primary}")
    if fallback and fallback.lower() not in PROVIDERS | {"none", "disabled"}:
        raise ValueError(f"unsupported fallback provider: {fallback}")

    fallback = fallback.lower()
    order = [primary]
    if fallback in PROVIDERS and fallback != primary:
        order.append(fallback)
    configured_models = models or {}
    attempts: list[str] = []

    for provider in order:
        api_key = keys.get(provider, "").strip()
        if not api_key:
            attempts.append(f"{provider}: missing key")
            continue
        try:
            payload = probe(provider, api_key)
        except ProviderProbeError as error:
            attempts.append(str(error))
            continue

        model_ids = _model_ids(provider, payload)
        if not model_ids:
            attempts.append(f"{provider}: no model IDs returned")
            continue

        configured_model = configured_models.get(provider, "").strip()
        if configured_model and configured_model not in model_ids:
            attempts.append(f"{provider}: configured model is not available")
            continue

        return JudgeSelection(
            provider=provider,
            model_id=configured_model or _default_model_id(provider, model_ids),
            fallback_from=primary if provider != primary else None,
            attempts=tuple(attempts),
        )

    raise NoUsableProvider("; ".join(attempts) or "no provider configured")


def _read_dotenv(path: Path = Path(".env")) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[name.strip()] = value
    return values


def _config_value(name: str, dotenv: dict[str, str], default: str = "") -> str:
    return os.environ.get(name, "").strip() or dotenv.get(name, "").strip() or default


def main() -> int:
    dotenv = _read_dotenv()
    openai_key = _config_value("OPENAI_API_KEY", dotenv)
    gemini_key = (
        _config_value("GEMINI_API_KEY", dotenv)
        or _config_value("GOOGLE_API_KEY", dotenv)
    )
    primary = _config_value("QC_JUDGE_PROVIDER", dotenv, "openai").lower()
    fallback = _config_value("QC_JUDGE_FALLBACK_PROVIDER", dotenv, "gemini").lower()
    preferred_models = {}
    if primary in PROVIDERS:
        preferred_models[primary] = _config_value("QC_JUDGE_MODEL", dotenv)
    if fallback in PROVIDERS and fallback != primary:
        preferred_models[fallback] = _config_value("QC_JUDGE_FALLBACK_MODEL", dotenv)

    try:
        selection = select_judge_provider(
            keys={"openai": openai_key, "gemini": gemini_key},
            primary=primary,
            fallback=fallback,
            models=preferred_models,
        )
    except (NoUsableProvider, ValueError) as error:
        print(f"STEP02: FAIL — {error}")
        return 1

    print(f"STEP02: PASS — provider={selection.provider}; model_id={selection.model_id}")
    if selection.fallback_from:
        first_failure = selection.attempts[0] if selection.attempts else f"{primary}: unavailable"
        print(f"Fallback selected: {first_failure} → {selection.provider}")
        print(
            "Set in .env to use this provider for DeepEval: "
            f"QC_JUDGE_PROVIDER={selection.provider}; QC_JUDGE_MODEL={selection.model_id}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
