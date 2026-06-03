"""apply_tcold_profile.py — apply a cold-start profile to config.yaml.

Reads the JSON produced by ``profile_cold_start.py`` (which exposes a flat
``t_cold_p95_seconds`` dict of ``{service: int}``) and rewrites
``temporal_gate.cold_times`` in the PBScaler ``config.yaml`` accordingly.

Only ``config[temporal_gate][cold_times]`` is touched; every other key,
and (when ruamel.yaml is installed) every comment, is preserved.

Usage:
  python scripts/apply_tcold_profile.py \\
      --profile t_cold_profile.json \\
      --config  autoscaler/config.yaml

  # Preview the change without writing anything:
  python scripts/apply_tcold_profile.py \\
      --profile t_cold_profile.json \\
      --config  autoscaler/config.yaml \\
      --dry-run

  # Write to a separate file instead of overwriting config.yaml:
  python scripts/apply_tcold_profile.py \\
      --profile t_cold_profile.json \\
      --config  autoscaler/config.yaml \\
      --out     config.patched.yaml

When ``--out`` overwrites an existing file, a ``<out>.bak`` snapshot of the
prior contents is written first.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

# Key in the profile JSON holding the flat {service: int} T_cold dict.
PROFILE_KEY = "t_cold_p95_seconds"
# Path within config.yaml that this tool is allowed to mutate.
GATE_KEY = "temporal_gate"
COLD_TIMES_KEY = "cold_times"

# Detect ruamel.yaml once. When present it round-trips comments and formatting.
try:
    from ruamel.yaml import YAML  # type: ignore

    _HAS_RUAMEL = True
except ImportError:  # pragma: no cover - exercised only without ruamel installed
    _HAS_RUAMEL = False

try:
    import yaml  # type: ignore

    _HAS_PYYAML = True
except ImportError:  # pragma: no cover - pyyaml is a declared dependency
    _HAS_PYYAML = False


def _fail(message: str) -> "NoReturn":  # type: ignore[name-defined]
    """Print an error to stderr and exit non-zero."""
    print(f"apply_tcold_profile: error: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_profile(profile_path: Path) -> dict[str, int]:
    """Load and validate the flat T_cold dict from the profile JSON."""
    if not profile_path.is_file():
        _fail(f"profile not found: {profile_path}")
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _fail(f"could not read profile {profile_path}: {exc}")

    if not isinstance(raw, dict):
        _fail(f"profile root must be a JSON object, got {type(raw).__name__}")
    if PROFILE_KEY not in raw:
        _fail(f"profile missing required key '{PROFILE_KEY}': {profile_path}")

    cold_times = raw[PROFILE_KEY]
    if not isinstance(cold_times, dict):
        _fail(f"'{PROFILE_KEY}' must be an object, got {type(cold_times).__name__}")
    if not cold_times:
        _fail(f"'{PROFILE_KEY}' is empty; nothing to apply")

    validated: dict[str, int] = {}
    for svc, value in cold_times.items():
        if not isinstance(svc, str):
            _fail(f"service name must be a string, got {svc!r}")
        # bool is a subclass of int; reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            _fail(f"cold time for '{svc}' must be an int, got {value!r}")
        if value < 1:
            _fail(f"cold time for '{svc}' must be >= 1, got {value}")
        validated[svc] = value
    return validated


def load_config(config_path: Path) -> tuple[Any, Any]:
    """Load config.yaml, returning (loaded_document, yaml_engine_or_None).

    With ruamel.yaml the returned engine is reused for dumping so comments
    survive the round trip; with the pyyaml fallback the engine is None.
    """
    if not config_path.is_file():
        _fail(f"config not found: {config_path}")
    text = config_path.read_text(encoding="utf-8")

    if _HAS_RUAMEL:
        yaml_engine = YAML()
        yaml_engine.preserve_quotes = True
        try:
            data = yaml_engine.load(text)
        except Exception as exc:  # ruamel raises various subclasses
            _fail(f"could not parse YAML {config_path}: {exc}")
        return data, yaml_engine

    if not _HAS_PYYAML:
        _fail("neither ruamel.yaml nor pyyaml is installed; cannot parse config")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _fail(f"could not parse YAML {config_path}: {exc}")
    return data, None


def apply_cold_times(config: Any, cold_times: dict[str, int]) -> dict[str, tuple[Any, int]]:
    """Update config[temporal_gate][cold_times] in place.

    Returns a diff mapping ``{service: (old_value_or_None, new_value)}`` for
    every service in the profile.
    """
    if not isinstance(config, dict):
        _fail(f"config root must be a mapping, got {type(config).__name__}")
    if GATE_KEY not in config or config[GATE_KEY] is None:
        _fail(f"config has no '{GATE_KEY}' section to update")

    gate = config[GATE_KEY]
    if not isinstance(gate, dict):
        _fail(f"'{GATE_KEY}' must be a mapping, got {type(gate).__name__}")

    existing = gate.get(COLD_TIMES_KEY)
    if existing is None:
        existing = {}
        gate[COLD_TIMES_KEY] = existing
    elif not isinstance(existing, dict):
        _fail(f"'{GATE_KEY}.{COLD_TIMES_KEY}' must be a mapping, got {type(existing).__name__}")

    diff: dict[str, tuple[Any, int]] = {}
    for svc, new_value in cold_times.items():
        old_value = existing.get(svc)
        diff[svc] = (old_value, new_value)
        existing[svc] = new_value
    return diff


def dump_config(config: Any, yaml_engine: Any) -> str:
    """Serialize the (possibly comment-bearing) config document to a string."""
    if yaml_engine is not None:
        import io

        buffer = io.StringIO()
        yaml_engine.dump(config, buffer)
        return buffer.getvalue()
    return yaml.safe_dump(config, sort_keys=False, default_flow_style=False)


def format_diff(diff: dict[str, tuple[Any, int]]) -> str:
    """Render the per-service change set as a human-readable block."""
    lines = [f"temporal_gate.{COLD_TIMES_KEY}:"]
    for svc in sorted(diff):
        old_value, new_value = diff[svc]
        if old_value is None:
            lines.append(f"  + {svc}: (unset) -> {new_value}")
        elif old_value == new_value:
            lines.append(f"    {svc}: {old_value} (unchanged)")
        else:
            lines.append(f"  ~ {svc}: {old_value} -> {new_value}")
    return "\n".join(lines)


def write_output(out_path: Path, content: str) -> None:
    """Write content to out_path, snapshotting any existing file as <out>.bak."""
    if out_path.exists():
        backup = out_path.with_suffix(out_path.suffix + ".bak")
        try:
            shutil.copy2(out_path, backup)
        except OSError as exc:
            _fail(f"could not write backup {backup}: {exc}")
        print(f"backup written: {backup}", file=sys.stderr)
    try:
        out_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        _fail(f"could not write output {out_path}: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply a cold-start profile (t_cold_p95_seconds) to "
        "config.yaml temporal_gate.cold_times."
    )
    parser.add_argument("--profile", required=True, type=Path,
                        help="profile JSON from profile_cold_start.py")
    parser.add_argument("--config", required=True, type=Path,
                        help="PBScaler config.yaml to update")
    parser.add_argument("--out", type=Path, default=None,
                        help="output path (default: overwrite --config)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the diff without writing")
    args = parser.parse_args(argv)

    cold_times = load_profile(args.profile)
    config, yaml_engine = load_config(args.config)
    diff = apply_cold_times(config, cold_times)

    engine_label = "ruamel.yaml (comments preserved)" if yaml_engine else "pyyaml"
    print(f"profile services: {len(cold_times)} | yaml engine: {engine_label}",
          file=sys.stderr)
    print(format_diff(diff))

    if args.dry_run:
        print("dry-run: no files written", file=sys.stderr)
        return 0

    out_path = args.out if args.out is not None else args.config
    content = dump_config(config, yaml_engine)
    write_output(out_path, content)
    print(f"wrote: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
