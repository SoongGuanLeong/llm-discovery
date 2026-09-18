#!/usr/bin/env python3
"""PROTOTYPE (#270) — `llm-discovery` CLI skeleton. Throwaway.

Answers one design question: does the command surface read well, and is the
failure wording actionable? See README.md beside this file for the transcripts,
the verdict and the open questions this surfaced.

What is real here (because it is the thing under test):

- the argparse tree and `--help` at every level
- the `--json` envelope on every code path, including usage errors, exceptions
  and Ctrl-C
- the exit taxonomy 0/1/2/3/4/130, 1:1 with `error.code`
- the stdout/stderr split
- `doctor`, `config status|set-key|clear-key`, `providers list`, `catalog *`
  and `export dry-run`

What is a surface-only stub: `discover`, `build`, `refresh`, `export apply`.
Each stub says so on stderr and emits the documented `data` shape with zero
counts. Nothing in this file calls the pipeline, writes `data/derived/`, or
touches `config/providers.yaml` for anything but a read.

Contract sources: issue #268 (envelope, exit codes, stdout/stderr, doctor) and
`docs/adr/0010-cli-replaces-ui-parity-contract.md` (flag deltas, secret entry).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

# --- bootstrap: make the repo's src importable without an install ------------
# The prototype must be trivial to run (`python prototypes/issue270/...`), and
# `--help` must work even when the package is not installed. Real module imports
# happen lazily inside the handlers that need them.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

DEFAULT_GATEWAY_URL = "http://localhost:20128"
DEFAULT_CONFIG = Path("config/providers.yaml")
DEFAULT_DATA_DIR = Path("data")
DEFAULT_RESULTS_DIR = Path("data/results")
DEFAULT_OUTPUT_DIR = Path("data/derived")
ENV_PATH = Path(".env")

CATALOG_FILES = (
    "models_dev_catalog.json",
    "artificial_analysis_models.json",
    "model_info_store.json",
)

# Only the gateway key is in scope for #267; provider API keys are out of scope.
_SECRET_NAMES = (
    "OMNIROUTE_API_KEY",
    "OMNIROUTE_MANAGE_KEY",
    "OMNIROUTE_TOKEN",
    "OMNIROUTE_AUTH_TOKEN",
)
SUPPORTED_SECRET = _SECRET_NAMES[0]

EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_PREREQUISITE = 3
EXIT_PIPELINE = 4
EXIT_INTERRUPTED = 130

_SCHEMA_VERSION = 1

# Ambient env as it was before any secret loading, so `config status` can report
# whether a key came from the shell, from .env, or from Infisical.
_AMBIENT_ENV = frozenset(os.environ)
_DOTENV_KEYS: set[str] = set()
_SECRETS_ERROR: str | None = None
_SECRETS_LOADED = False

# Names the unit in flight, so a Ctrl-C envelope can say what was interrupted.
_CURRENT_UNIT = ""


# --------------------------------------------------------------------------- #
# Errors and results
# --------------------------------------------------------------------------- #


class CliError(Exception):
    code = "internal"
    exit_code = EXIT_INTERNAL

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(CliError):
    code = "usage"
    exit_code = EXIT_USAGE


class PrerequisiteError(CliError):
    code = "prerequisite"
    exit_code = EXIT_PREREQUISITE


class PipelineError(CliError):
    code = "pipeline"
    exit_code = EXIT_PIPELINE


@dataclass
class Result:
    command: str
    data: Any
    human: str
    error: dict[str, Any] | None = None
    exit_code: int = 0


@dataclass
class Check:
    name: str
    severity: str  # required | advisory
    status: str  # pass | warn | fail
    detail: str
    fix: str


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


class _Parser(argparse.ArgumentParser):
    """argparse that raises a usage error instead of calling sys.exit(2)."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message, hint=f"run `{self.prog} --help`")


_COMMON = argparse.ArgumentParser(add_help=False)
_COMMON.add_argument(
    "--json",
    dest="json_mode",
    action="store_true",
    default=argparse.SUPPRESS,
    help="Emit one machine-readable JSON envelope on stdout at exit.",
)

_CONFIG_PARENT = argparse.ArgumentParser(add_help=False)
_CONFIG_PARENT.add_argument(
    "--config",
    type=Path,
    default=DEFAULT_CONFIG,
    help="Providers YAML path (default: config/providers.yaml).",
)

_DATA_PARENT = argparse.ArgumentParser(add_help=False)
_DATA_PARENT.add_argument(
    "--data-dir",
    type=Path,
    default=DEFAULT_DATA_DIR,
    help="Data directory (default: data).",
)

_EXPORT_PARENT = argparse.ArgumentParser(add_help=False)
_EXPORT_PARENT.add_argument(
    "--gateway-url",
    default=None,
    help=f"OmniRoute base URL (default: $OMNIROUTE_URL or {DEFAULT_GATEWAY_URL}).",
)
_EXPORT_PARENT.add_argument(
    "--api-key",
    default=None,
    help="Management API key. Prefer OMNIROUTE_API_KEY: argv leaks into `ps` and shell history.",
)
_EXPORT_PARENT.add_argument(
    "--providers", type=Path, default=DEFAULT_CONFIG, help="providers.yaml path."
)
_EXPORT_PARENT.add_argument(
    "--results-dir", type=Path, default=DEFAULT_RESULTS_DIR, help="data/results dir."
)
_EXPORT_PARENT.add_argument(
    "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="output dir (default: data/derived)."
)


def _add_secret_name_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "name",
        help=f"Secret name. Only {SUPPORTED_SECRET} is supported in this effort.",
    )


def _build_parser() -> _Parser:
    parser = _Parser(
        prog="llm-discovery",
        parents=[_COMMON],
        description="Discover, build and export a curated LLM keep-list, then apply it to OmniRoute.",
    )

    sub = parser.add_subparsers(dest="group", required=True, metavar="<command>")

    # --- config ------------------------------------------------------------ #
    config = sub.add_parser(
        "config",
        parents=[_COMMON, _CONFIG_PARENT],
        help="Inspect or change local configuration.",
    )
    config_sub = config.add_subparsers(dest="action", required=True, metavar="<action>")

    status = config_sub.add_parser(
        "status", parents=[_COMMON], help="Show config path, provider count, key presence and gateway URL."
    )
    status.set_defaults(handler=_cmd_config_status, command_path="config.status")

    set_key = config_sub.add_parser(
        "set-key", parents=[_COMMON], help="Set a secret from stdin (never from argv)."
    )
    _add_secret_name_argument(set_key)
    set_key.set_defaults(handler=_cmd_config_set_key, command_path="config.set-key")

    clear_key = config_sub.add_parser(
        "clear-key", parents=[_COMMON], help="Remove a secret from .env and the current environment."
    )
    _add_secret_name_argument(clear_key)
    clear_key.set_defaults(handler=_cmd_config_clear_key, command_path="config.clear-key")

    # --- providers --------------------------------------------------------- #
    providers = sub.add_parser(
        "providers",
        parents=[_COMMON, _CONFIG_PARENT],
        help="Inspect the Configured Provider set (config/providers.yaml).",
    )
    providers_sub = providers.add_subparsers(dest="action", required=True, metavar="<action>")
    providers_list = providers_sub.add_parser(
        "list", parents=[_COMMON], help="List configured providers with their secret env-var names."
    )
    providers_list.set_defaults(handler=_cmd_providers_list, command_path="providers.list")

    # --- discover ---------------------------------------------------------- #
    discover = sub.add_parser(
        "discover",
        parents=[_COMMON, _CONFIG_PARENT],
        help="Discover and evaluate providers (no provider means all configured providers).",
    )
    discover.add_argument("provider", nargs="?", default=None, help="Provider to evaluate (tracer).")
    discover.add_argument("--all", action="store_true", help="Evaluate every model for <provider> (batch).")
    discover.add_argument("-w", "--workers", type=int, default=4, help="Parallel judge calls per provider (default: 4).")
    discover.add_argument("--force-judge", action="store_true", help="Bypass stored Keeper/Candidate reuse and re-judge.")
    discover.set_defaults(handler=_cmd_discover, command_path="discover")

    # --- build ------------------------------------------------------------- #
    build = sub.add_parser(
        "build",
        parents=[_COMMON, _CONFIG_PARENT],
        help="Build the store from discovery results (cache-optional, atomic).",
    )
    build.add_argument("--providers", nargs="*", default=None, help="Subset of provider names.")
    build.add_argument("--workers", type=int, default=8, help="Workers per provider (default: 8).")
    build.add_argument("--catalog-max-age-days", type=int, default=28, help="Refresh catalogs older than this (0 disables).")
    build.add_argument("--no-catalog-refresh", action="store_true", help="Skip the catalog freshness gate (offline).")
    build.add_argument("--retry-failed", action="store_true", help="Only rebuild providers that failed last run.")
    build.add_argument("--provider-concurrency", type=int, default=None, help="Max providers in parallel (default 4).")
    build.add_argument("--judge-timeout", type=int, default=None, help="Override judge LLM timeout in seconds.")
    build.add_argument("--force-judge", action="store_true", help="Rebuild with a fresh LLM judge.")
    build.set_defaults(handler=_cmd_build, command_path="build")

    # --- export ------------------------------------------------------------ #
    export = sub.add_parser(
        "export",
        parents=[_COMMON, _EXPORT_PARENT],
        help="Generate the OmniRoute payload; apply it to the gateway.",
    )
    export_sub = export.add_subparsers(dest="mode", required=True, metavar="<mode>")

    dry_run = export_sub.add_parser(
        "dry-run", parents=[_COMMON, _EXPORT_PARENT], help="Generate the payload locally, no network, no writes."
    )
    dry_run.set_defaults(handler=_cmd_export_dry_run, command_path="export.dry-run")

    apply = export_sub.add_parser(
        "apply", parents=[_COMMON, _EXPORT_PARENT], help="POST the payload to the OmniRoute gateway."
    )
    apply.set_defaults(handler=_cmd_export_apply, command_path="export.apply")

    # --- refresh ----------------------------------------------------------- #
    refresh = sub.add_parser(
        "refresh",
        parents=[_COMMON, _DATA_PARENT],
        help="Refresh the catalog snapshots (AA + models.dev + benchmarks).",
    )
    refresh.add_argument("--aa-url", default="https://artificialanalysis.ai/api/v2/data/llms/models", help="AA API URL.")
    refresh.add_argument("--models-dev-url", default="https://models.dev/catalog.json", help="models.dev catalog URL.")
    refresh.add_argument("--no-backup", action="store_true", help="Disable the .bak backup.")
    refresh.add_argument("--dry-run", action="store_true", help="Fetch and validate but do not write.")
    refresh.add_argument(
        "--only",
        nargs="*",
        choices=["aa", "models_dev", "benchmarks"],
        help="Only refresh the selected catalogs.",
    )
    refresh.set_defaults(handler=_cmd_refresh, command_path="refresh")

    # --- catalog ----------------------------------------------------------- #
    catalog = sub.add_parser(
        "catalog",
        parents=[_COMMON, _DATA_PARENT],
        help="Query the offline catalog snapshots (models.dev, Artificial Analysis).",
    )
    catalog_sub = catalog.add_subparsers(dest="source", required=True, metavar="<source>")

    aa = catalog_sub.add_parser("aa", parents=[_COMMON], help="Query Artificial Analysis.")
    aa_sub = aa.add_subparsers(dest="action", required=True, metavar="<action>")
    aa_search = aa_sub.add_parser("search", parents=[_COMMON], help="Search models by name.")
    aa_search.add_argument("query")
    aa_search.set_defaults(handler=_cmd_catalog_aa_search, command_path="catalog.aa.search")
    aa_filter = aa_sub.add_parser("filter", parents=[_COMMON], help="Filter models by intelligence score.")
    aa_filter.add_argument("--min-score", type=float, default=25)
    aa_filter.set_defaults(handler=_cmd_catalog_aa_filter, command_path="catalog.aa.filter")

    models = catalog_sub.add_parser("models", parents=[_COMMON], help="Query models.dev models.")
    models_sub = models.add_subparsers(dest="action", required=True, metavar="<action>")
    models_show = models_sub.add_parser("show", parents=[_COMMON], help="Show one model.")
    models_show.add_argument("model_id")
    models_show.set_defaults(handler=_cmd_catalog_models_show, command_path="catalog.models.show")
    models_providers = models_sub.add_parser("providers", parents=[_COMMON], help="Show providers offering a model.")
    models_providers.add_argument("model_id")
    models_providers.set_defaults(handler=_cmd_catalog_models_providers, command_path="catalog.models.providers")

    cat_providers = catalog_sub.add_parser("providers", parents=[_COMMON], help="Query models.dev providers.")
    cat_providers_sub = cat_providers.add_subparsers(dest="action", required=True, metavar="<action>")
    cat_provider_show = cat_providers_sub.add_parser("show", parents=[_COMMON], help="Show one provider.")
    cat_provider_show.add_argument("provider_id")
    cat_provider_show.set_defaults(handler=_cmd_catalog_providers_show, command_path="catalog.providers.show")
    cat_provider_models = cat_providers_sub.add_parser("models", parents=[_COMMON], help="Show models offered by a provider.")
    cat_provider_models.add_argument("provider_id")
    cat_provider_models.set_defaults(handler=_cmd_catalog_providers_models, command_path="catalog.providers.models")

    # --- doctor ------------------------------------------------------------ #
    doctor = sub.add_parser(
        "doctor",
        parents=[_COMMON, _CONFIG_PARENT, _DATA_PARENT],
        help="Check the Golden Path prerequisites and report actionable failures.",
    )
    doctor.add_argument(
        "--gateway-url",
        default=None,
        help=f"OmniRoute base URL to probe (default: $OMNIROUTE_URL or {DEFAULT_GATEWAY_URL}).",
    )
    doctor.set_defaults(handler=_cmd_doctor, command_path="doctor")

    return parser


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _note(text: str) -> None:
    """Non-fatal notice: stderr, never stdout, never part of the envelope."""
    sys.stderr.write(text + "\n")


def _stub(command: str) -> None:
    _note(f"[prototype] {command} is not wired to the pipeline; surface only, nothing ran")


def _mask(value: str) -> str:
    """Last four characters only. Never returns more of the secret than that."""
    if not value:
        return ""
    return "***" + value[-4:] if len(value) >= 4 else "***"


def _gateway_key() -> str | None:
    for name in _SECRET_NAMES:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _gateway_url(explicit: str | None) -> str:
    if explicit and explicit.strip():
        url = explicit.strip()
        if not re.match(r"^https?://", url):
            raise UsageError(
                f"invalid gateway URL {url!r}",
                hint="the gateway URL must start with http:// or https://",
            )
        return url
    from_env = os.environ.get("OMNIROUTE_URL", "").strip()
    return from_env or DEFAULT_GATEWAY_URL


def _load_secrets(infisical_config: Any = None) -> None:
    """Ambient env, then .env, then Infisical when its project ids are set.

    `infisical_config` is the config's `infisical` section. It is None when the
    config could not be read, in which case Infisical is skipped: the config
    check already reports the actionable failure and Infisical cannot be
    addressed without the env-var names the config supplies.
    """
    global _SECRETS_ERROR, _SECRETS_LOADED, _CURRENT_UNIT
    if _SECRETS_LOADED:
        return
    _SECRETS_LOADED = True

    before = set(os.environ)
    try:
        from dotenv import load_dotenv

        # Explicit path: `load_dotenv()` with no argument resolves .env relative
        # to the *calling module's file*, not the cwd (python-dotenv's
        # find_dotenv walks up from __file__). Every other path in this CLI is
        # cwd-relative, so an implicit load would read a different .env than
        # `config set-key` writes. #271 must pick one anchoring rule.
        if ENV_PATH.exists():
            load_dotenv(dotenv_path=ENV_PATH)
    except Exception as exc:  # pragma: no cover - prototype
        _SECRETS_ERROR = f"dotenv load failed: {exc}"
    _DOTENV_KEYS.update(set(os.environ) - before)

    if infisical_config is None:
        return
    try:
        from llm_discovery.secrets import load_all_secrets
    except Exception:
        return
    if not (
        os.environ.get(infisical_config.shared_project_id_env)
        or os.environ.get(infisical_config.discovery_project_id_env)
    ):
        return  # Infisical is opt-in; plain env vars still work.
    _CURRENT_UNIT = "secret load: infisical export"
    try:
        load_all_secrets(infisical_config)
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        _SECRETS_ERROR = f"infisical export failed: {str(exc)[:200]}"
    _CURRENT_UNIT = ""


def _secret_state(name: str) -> tuple[str, str | None]:
    if name in os.environ:
        if name in _AMBIENT_ENV:
            return "set", "env"
        if name in _DOTENV_KEYS:
            return "set", "dotenv"
        return "set", "infisical"
    return "unset", None


def _load_config_or_fail(path: Path) -> Any:
    from llm_discovery.config import load_config

    if not path.exists():
        raise PrerequisiteError(
            f"{path} not found",
            hint=(
                "create config/providers.yaml with at least one provider "
                "(see README -> Prerequisites); this file is user-only, so ask the user to edit it"
            ),
        )
    try:
        config = load_config(path)
    except Exception as exc:
        raise PrerequisiteError(
            f"{path} is invalid: {exc}",
            hint="fix config/providers.yaml; this file is user-only, so ask the user to edit it",
        ) from exc
    if not config.providers:
        raise PrerequisiteError(
            f"{path} defines no providers",
            hint="add at least one provider to config/providers.yaml; this file is user-only, so ask the user to edit it",
        )
    return config


def _catalog_path(data_dir: Path, filename: str) -> Path:
    path = data_dir / filename
    if not path.exists():
        raise PrerequisiteError(
            f"catalog not found: {path}",
            hint="run `llm-discovery refresh` to fetch the catalogs",
        )
    return path


def _guess_command(argv: list[str]) -> str:
    """Best-effort dotted command path, used only when argparse itself failed."""
    words = [a for a in argv if not a.startswith("-")]
    return ".".join(words[:2]) if words else "unknown"


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #


def _cmd_config_status(args: argparse.Namespace) -> Result:
    config = _load_config_or_fail(args.config)
    _load_secrets(config.infisical)
    keys = []
    for name in _SECRET_NAMES:
        status, source = _secret_state(name)
        keys.append({"name": name, "status": status, "source": source})
    gateway_url = _gateway_url(None)
    data = {
        "config_path": str(args.config),
        "providers": len(config.providers),
        "keys": keys,
        "gateway_url": gateway_url,
    }
    lines = [f"config:    {args.config}", f"providers: {len(config.providers)}"]
    for key in keys:
        suffix = f" (source: {key['source']})" if key["status"] == "set" else ""
        lines.append(f"  {key['name']}: {key['status']}{suffix}")
    lines.append(f"gateway:   {gateway_url}")
    lines.append(
        "note: `set` means the key is present, not that it works. "
        "Run `llm-discovery doctor` to validate it."
    )
    return Result("config.status", data, "\n".join(lines))


def _read_secret_from_stdin() -> str:
    if sys.stdin.isatty():
        import getpass

        return getpass.getpass("key: ")
    return sys.stdin.read()


def _cmd_config_set_key(args: argparse.Namespace) -> Result:
    if args.name != SUPPORTED_SECRET:
        raise UsageError(
            f"unsupported secret {args.name!r}",
            hint=f"only {SUPPORTED_SECRET} is in scope; provider API keys are not managed by this command yet",
        )
    value = _read_secret_from_stdin().strip()
    if not value:
        raise UsageError(
            "no key value on stdin",
            hint=f"pipe the value in: printf %s \"$KEY\" | llm-discovery config set-key {args.name}",
        )
    from dotenv import set_key

    set_key(str(ENV_PATH), args.name, value)
    ENV_PATH.chmod(0o600)  # always, not only when the file was created (ADR 0010 #4)
    os.environ[args.name] = value
    data = {
        "name": args.name,
        "status": "set",
        "path": str(ENV_PATH),
        "masked": _mask(value),
    }
    return Result("config.set-key", data, f"{args.name}: set in {ENV_PATH} ({_mask(value)})")


def _env_file_has_key(name: str) -> bool:
    """True when .env has a line assigning `name` (so unset_key is not a no-op)."""
    if not ENV_PATH.exists():
        return False
    try:
        text = ENV_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        head = stripped.split("=", 1)[0].strip()
        if head.startswith("export "):
            head = head[len("export "):].strip()
        if head == name:
            return True
    return False


def _cmd_config_clear_key(args: argparse.Namespace) -> Result:
    if args.name != SUPPORTED_SECRET:
        raise UsageError(
            f"unsupported secret {args.name!r}",
            hint=f"only {SUPPORTED_SECRET} is in scope; provider API keys are not managed by this command yet",
        )
    if _env_file_has_key(args.name):
        from dotenv import unset_key

        try:
            unset_key(str(ENV_PATH), args.name)
        except Exception as exc:
            raise PrerequisiteError(
                f"could not remove {args.name} from {ENV_PATH}: {exc}",
                hint=f"edit {ENV_PATH} by hand and delete the {args.name} line",
            ) from exc
    os.environ.pop(args.name, None)
    data = {"name": args.name, "status": "unset", "path": str(ENV_PATH), "masked": ""}
    return Result("config.clear-key", data, f"{args.name}: unset ({ENV_PATH})")


# --------------------------------------------------------------------------- #
# providers
# --------------------------------------------------------------------------- #


def _cmd_providers_list(args: argparse.Namespace) -> Result:
    config = _load_config_or_fail(args.config)
    rows = [
        {
            "name": p.name,
            "base_url": p.base_url,
            "secret": p.secret,  # the env-var NAME, never its value
            "discovery": p.discovery,
            "discovery_strategy": p.discovery_strategy,
            "custom": p.custom,
        }
        for p in config.providers
    ]
    rows.sort(key=lambda row: row["name"])
    data = {"count": len(rows), "providers": rows}
    lines = [f"{len(rows)} configured providers ({args.config})"]
    for row in rows:
        lines.append(
            f"  {row['name']:<18} {row['discovery']:<8} secret={row['secret']:<24} {row['base_url'] or ''}".rstrip()
        )
    return Result("providers.list", data, "\n".join(lines))


# --------------------------------------------------------------------------- #
# discover / build / refresh / export apply — stubs
# --------------------------------------------------------------------------- #


def _cmd_discover(args: argparse.Namespace) -> Result:
    config = _load_config_or_fail(args.config)
    configured = [p.name for p in config.providers]
    if args.provider is not None and args.provider not in configured:
        raise UsageError(
            f"unknown provider {args.provider!r}",
            hint=f"configured providers: {', '.join(configured)}",
        )
    if args.all and args.provider is None:
        raise UsageError(
            "--all needs a provider",
            hint="use `llm-discovery discover <provider> --all`, or omit --all to discover every configured provider",
        )
    if args.provider is None:
        scope = f"all {len(configured)} configured providers"
    elif args.all:
        scope = f"all models for {args.provider}"
    else:
        scope = f"tracer: one model for {args.provider}"
    _stub("discover")
    data = {"provider": args.provider, "completed": 0, "total": 0, "results": []}
    return Result("discover", data, f"discover: {scope} — not wired in this prototype")


def _cmd_build(args: argparse.Namespace) -> Result:
    _load_config_or_fail(args.config)
    _stub("build")
    data = {
        "completed": 0,
        "total": 0,
        "totals": {"keepers": 0, "candidates": 0, "reused": 0, "rebuilt": 0},
        "providers": [],
    }
    return Result("build", data, "build: not wired in this prototype")


def _cmd_refresh(args: argparse.Namespace) -> Result:
    _stub("refresh")
    return Result("refresh", {"catalogs": []}, "refresh: not wired in this prototype")


def _cmd_export_apply(args: argparse.Namespace) -> Result:
    if not args.api_key and not _gateway_key():
        raise PrerequisiteError(
            "no gateway API key",
            hint=(
                f"run `llm-discovery config set-key {SUPPORTED_SECRET}`, or pass --api-key "
                "(prefer the environment variable: argv leaks into `ps` and shell history)"
            ),
        )
    _stub("export apply")
    gateway_url = _gateway_url(args.gateway_url)
    data = {"gateway_url": gateway_url, "applied": 0, "results": []}
    return Result("export.apply", data, f"export apply: not wired in this prototype (target {gateway_url})")


# --------------------------------------------------------------------------- #
# export dry-run — real
# --------------------------------------------------------------------------- #


def _cmd_export_dry_run(args: argparse.Namespace) -> Result:
    if not args.providers.exists():
        raise PrerequisiteError(
            f"{args.providers} not found",
            hint="create config/providers.yaml (user-only; ask the user to edit it)",
        )
    if not args.results_dir.exists():
        raise PrerequisiteError(
            f"{args.results_dir} not found",
            hint="run `llm-discovery discover` or `llm-discovery build` first",
        )
    from llm_discovery.omniroute_export import generate_payload

    try:
        payload = generate_payload(args.providers, args.results_dir)
    except Exception as exc:
        raise PipelineError(
            f"export dry-run failed: {exc}",
            hint="check config/providers.yaml and data/results/*.yaml",
        ) from exc
    _note("[prototype] export dry-run did not write data/derived/*.json (prototype)")
    combos = payload.get("combos") or []
    targets = sum(len(c.get("models") or []) for c in combos)
    data = payload
    human = (
        f"export dry-run: {len(payload.get('import') or [])} import rows, "
        f"{len(payload.get('models') or [])} model entries, "
        f"{len(combos)} combos ({targets} targets), "
        f"{len(payload.get('gc') or {})} providers in the GC plan"
    )
    return Result("export.dry-run", data, human)


# --------------------------------------------------------------------------- #
# catalog
# --------------------------------------------------------------------------- #


def _cmd_catalog_aa_search(args: argparse.Namespace) -> Result:
    from llm_discovery.catalogs import ArtificialAnalysisCatalog

    catalog = ArtificialAnalysisCatalog(_catalog_path(args.data_dir, "artificial_analysis_models.json"))
    models = catalog.search(args.query)
    return Result("catalog.aa.search", {"count": len(models), "models": models}, _render_aa(models))


def _cmd_catalog_aa_filter(args: argparse.Namespace) -> Result:
    from llm_discovery.catalogs import ArtificialAnalysisCatalog

    catalog = ArtificialAnalysisCatalog(_catalog_path(args.data_dir, "artificial_analysis_models.json"))
    models = catalog.filter(min_score=args.min_score)
    return Result("catalog.aa.filter", {"count": len(models), "models": models}, _render_aa(models))


def _render_aa(models: list[dict[str, Any]]) -> str:
    lines = []
    for model in models:
        score = model.get("evaluations", {}).get("artificial_analysis_intelligence_index")
        lines.append(f"{model['name']} | {score}")
    return "\n".join(lines) if lines else "no models matched"


def _cmd_catalog_models_show(args: argparse.Namespace) -> Result:
    from llm_discovery.catalogs import ModelsDevCatalog

    catalog = ModelsDevCatalog(_catalog_path(args.data_dir, "models_dev_catalog.json"))
    model = catalog.get_model(args.model_id)
    if model is None:
        raise UsageError(
            f"model not found: {args.model_id}",
            hint="check the id, or run `llm-discovery refresh` if the catalog is stale",
        )
    return Result("catalog.models.show", {"count": 1, "models": [model]}, json.dumps(model, indent=2, sort_keys=True))


def _cmd_catalog_models_providers(args: argparse.Namespace) -> Result:
    from llm_discovery.catalogs import ModelsDevCatalog

    catalog = ModelsDevCatalog(_catalog_path(args.data_dir, "models_dev_catalog.json"))
    providers = catalog.providers_for_model(args.model_id)
    if not providers:
        raise UsageError(
            f"no providers found for {args.model_id}",
            hint="check the id, or run `llm-discovery refresh` if the catalog is stale",
        )
    lines = [f"{p['id']} | {p['name']} | {p['api']}" for p in providers]
    return Result("catalog.models.providers", {"count": len(providers), "models": providers}, "\n".join(lines))


def _cmd_catalog_providers_show(args: argparse.Namespace) -> Result:
    from llm_discovery.catalogs import ModelsDevCatalog

    catalog = ModelsDevCatalog(_catalog_path(args.data_dir, "models_dev_catalog.json"))
    provider = catalog.get_provider(args.provider_id)
    if provider is None:
        raise UsageError(
            f"provider not found: {args.provider_id}",
            hint="check the id, or run `llm-discovery refresh` if the catalog is stale",
        )
    return Result(
        "catalog.providers.show", {"count": 1, "models": [provider]}, json.dumps(provider, indent=2, sort_keys=True)
    )


def _cmd_catalog_providers_models(args: argparse.Namespace) -> Result:
    from llm_discovery.catalogs import ModelsDevCatalog

    catalog = ModelsDevCatalog(_catalog_path(args.data_dir, "models_dev_catalog.json"))
    models = catalog.models_for_provider(args.provider_id)
    if not models:
        raise UsageError(
            f"provider not found or has no models: {args.provider_id}",
            hint="check the id, or run `llm-discovery refresh` if the catalog is stale",
        )
    rows = [{"id": model_id, **model} for model_id, model in models.items()]
    lines = [f"{model_id} | {model['name']}" for model_id, model in models.items()]
    return Result("catalog.providers.models", {"count": len(rows), "models": rows}, "\n".join(lines))


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def _check_python() -> Check:
    version = platform.python_version()
    if sys.version_info >= (3, 12):
        return Check("python", "required", "pass", version, "")
    return Check(
        "python",
        "required",
        "fail",
        f"{version} found, requires >= 3.12",
        "install Python 3.12 and recreate the venv: `uv venv --python 3.12 && uv pip install -e .`",
    )


def _check_installed() -> Check:
    spec = importlib.util.find_spec("llm_discovery")
    exe = shutil.which("llm-discovery")
    if spec and exe:
        origin = getattr(spec, "origin", "?")
        return Check("install", "required", "pass", f"llm_discovery importable from {origin}; llm-discovery at {exe}", "")
    missing = []
    if not spec:
        missing.append("`llm_discovery` is not importable")
    if not exe:
        missing.append("`llm-discovery` is not on PATH")
    return Check(
        "install",
        "required",
        "fail",
        "; ".join(missing),
        "from the repo root run `pip install -e .` (or `uv pip install -e .`), then reopen the shell",
    )


def _check_config(path: Path) -> Check:
    from llm_discovery.config import load_config

    if not path.exists():
        return Check(
            "config",
            "required",
            "fail",
            f"{path} not found",
            "create config/providers.yaml with at least one provider (see README -> Prerequisites); "
            "this file is user-only, so ask the user to edit it",
        )
    try:
        config = load_config(path)
    except Exception as exc:
        return Check(
            "config",
            "required",
            "fail",
            f"{path} is invalid: {exc}",
            "fix config/providers.yaml; this file is user-only, so ask the user to edit it",
        )
    if not config.providers:
        return Check(
            "config",
            "required",
            "fail",
            f"{path} defines no providers",
            "add at least one provider to config/providers.yaml; this file is user-only, so ask the user to edit it",
        )
    return Check("config", "required", "pass", f"{path} valid, {len(config.providers)} providers", "")


def _check_secret() -> Check:
    status, source = _secret_state(SUPPORTED_SECRET)
    if status == "set":
        return Check(
            "secret",
            "required",
            "pass",
            f"{SUPPORTED_SECRET} set via {source} ({_mask(os.environ[SUPPORTED_SECRET])})",
            "",
        )
    detail = f"{SUPPORTED_SECRET} is not set"
    if _SECRETS_ERROR:
        detail += f"; {_SECRETS_ERROR}"
    fix = f"run `llm-discovery config set-key {SUPPORTED_SECRET}` (reads the value from stdin, never from argv)"
    if _SECRETS_ERROR:
        fix += (
            "; or fix Infisical: run `infisical login`, or unset LLM_SHARED_PROJECT_ID / "
            "LLM_DISCOVERY_PROJECT_ID to use plain environment variables"
        )
    return Check("secret", "required", "fail", detail, fix)


def _check_catalogs(data_dir: Path) -> Check:
    missing = [name for name in CATALOG_FILES if not (data_dir / name).exists()]
    if not missing:
        return Check("catalogs", "advisory", "pass", f"all {len(CATALOG_FILES)} catalogs present in {data_dir}/", "")
    return Check(
        "catalogs",
        "advisory",
        "warn",
        f"missing: {', '.join(missing)}",
        "run `llm-discovery refresh` for the catalogs, then `llm-discovery build` to populate model_info_store.json",
    )


def _check_gateway(url: str) -> Check:
    global _CURRENT_UNIT
    try:
        import httpx
    except Exception:
        return Check("gateway", "required", "fail", "httpx is not installed", "run `pip install -e .`")

    probe = f"{url}/api/combos"
    headers = {}
    key = _gateway_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    _CURRENT_UNIT = "doctor: gateway probe"
    try:
        response = httpx.get(probe, headers=headers, timeout=5.0)
    except KeyboardInterrupt:
        raise  # leave _CURRENT_UNIT set so the envelope can name what was interrupted
    except Exception as exc:
        _CURRENT_UNIT = ""
        name = type(exc).__name__
        if "Connect" in name or "Connection" in name:
            detail = f"connection refused at {url}"
        elif "Timeout" in name:
            detail = f"timed out after 5s at {url}"
        else:
            detail = f"{name}: {exc}"
        return Check("gateway", "required", "fail", detail, "start the OmniRoute gateway, then re-run `llm-discovery doctor`")
    _CURRENT_UNIT = ""

    status = response.status_code
    if status == 200:
        return Check("gateway", "required", "pass", f"HTTP 200 from {probe}", "")
    if status in (401, 403):
        return Check(
            "gateway",
            "required",
            "fail",
            f"HTTP {status} from {probe} — the gateway rejected the key",
            f"run `llm-discovery config set-key {SUPPORTED_SECRET}` with a valid management key",
        )
    return Check(
        "gateway",
        "required",
        "fail",
        f"HTTP {status} from {probe}",
        "check that the URL is the OmniRoute gateway and that the gateway is healthy",
    )


_TAGS = {"pass": "[ ok ]", "warn": "[warn]", "fail": "[fail]"}


def _cmd_doctor(args: argparse.Namespace) -> Result:
    try:
        config: Any = _load_config_or_fail(args.config)
    except PrerequisiteError:
        config = None  # the config check reports it; skip Infisical without the env-var names
    _load_secrets(config.infisical if config else None)
    gateway_url = _gateway_url(args.gateway_url)
    checks = [
        _check_python(),
        _check_installed(),
        _check_config(args.config),
        _check_secret(),
        _check_catalogs(args.data_dir),
        _check_gateway(gateway_url),
    ]
    failed = [c for c in checks if c.severity == "required" and c.status == "fail"]
    warned = [c for c in checks if c.status == "warn"]
    ok = not failed

    lines = []
    for check in checks:
        lines.append(f"{_TAGS[check.status]} {check.name} — {check.detail}")
        if check.fix and check.status in ("warn", "fail"):
            lines.append(f"  fix: {check.fix}")
    if ok:
        summary = "doctor: ready"
        if warned:
            summary += f" ({len(warned)} advisory warning(s))"
        lines.append(summary)
        error = None
        exit_code = 0
    else:
        names = ", ".join(c.name for c in failed)
        lines.append(f"doctor: not ready — {len(failed)} required check(s) failed: {names}")
        error = {
            "code": "prerequisite",
            "message": f"{len(failed)} required check(s) failed: {names}",
            "hint": "each failed check carries a `fix`; apply it, then re-run `llm-discovery doctor`",
        }
        exit_code = EXIT_PREREQUISITE

    data = {"ok": ok, "checks": [asdict(c) for c in checks]}
    return Result("doctor", data, "\n".join(lines), error=error, exit_code=exit_code)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _emit_json(command: str, data: Any, error: dict[str, Any] | None) -> None:
    payload = {
        "schema": _SCHEMA_VERSION,
        "ok": error is None,
        "command": command,
        "data": data,
        "error": error,
    }
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    sys.stdout.flush()


def _report_failure(command: str, json_mode: bool, error: CliError, data: Any = None) -> int:
    envelope_error = {"code": error.code, "message": error.message, "hint": error.hint}
    if json_mode:
        _emit_json(command, data, envelope_error)
    else:
        sys.stderr.write(f"error: {error.message}\n")
        if error.hint:
            sys.stderr.write(f"hint: {error.hint}\n")
    return error.exit_code


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point. Returns the exit code."""
    try:
        return _run(argv)
    except BrokenPipeError:
        # A consumer closed stdout (e.g. `| head`). Exit quietly, no traceback.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except Exception:
            pass
        return 0


def _run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in argv

    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:  # --help
        return int(exc.code or 0)
    except CliError as exc:
        return _report_failure(_guess_command(argv), json_mode, exc)

    command = getattr(args, "command_path", _guess_command(argv))
    handler: Callable[[argparse.Namespace], Result] | None = getattr(args, "handler", None)
    if handler is None:
        return _report_failure(command, json_mode, UsageError("no command given", hint="run `llm-discovery --help`"))

    try:
        result = handler(args)
    except KeyboardInterrupt:
        interrupted = CliError(
            f"interrupted during {_CURRENT_UNIT or command}",
            hint="nothing was left half-written; re-run when ready",
        )
        interrupted.code = "interrupted"
        interrupted.exit_code = EXIT_INTERRUPTED
        return _report_failure(command, json_mode, interrupted)
    except CliError as exc:
        return _report_failure(command, json_mode, exc)
    except Exception as exc:  # unexpected bug
        return _report_failure(
            command,
            json_mode,
            CliError(f"{type(exc).__name__}: {exc}", hint="this is a bug in llm-discovery"),
        )

    if json_mode:
        _emit_json(command, result.data, result.error)
    else:
        if result.human:
            sys.stdout.write(result.human + "\n")
        if result.error:
            sys.stderr.write(f"error: {result.error['message']}\n")
            if result.error.get("hint"):
                sys.stderr.write(f"hint: {result.error['hint']}\n")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
