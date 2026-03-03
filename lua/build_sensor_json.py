#!/usr/bin/env python3
import argparse
import base64
import json
import re
from pathlib import Path
from typing import Dict, List, Optional


CONST_PATTERN = re.compile(r"^\s*local\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)\s*$")
INSERT_BLOCK_PATTERN = re.compile(
    r"table\.insert\s*\(\s*result\s*,\s*\{(.*?)\}\s*\)",
    re.DOTALL,
)
FIELD_PATTERN_TEMPLATE = r"\b{field}\b\s*=\s*([^,\n]+)"


def _parse_numeric(text: str) -> Optional[int]:
    text = text.strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.0+", text):
        return int(float(text))
    return None


def _title_from_var(var_name: str) -> str:
    name = var_name
    for token in ("resource", "object", "instance", "rs485"):
        name = re.sub(rf"(^|_){token}(_|$)", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        return "Sensor Value"
    return name.replace("_", " ").title()


def extract_lwm2m_objects(lua_text: str) -> List[Dict]:
    constants: Dict[str, int] = {}
    for line in lua_text.splitlines():
        m = CONST_PATTERN.match(line)
        if not m:
            continue
        key, value_text = m.groups()
        number = _parse_numeric(value_text)
        if number is not None:
            constants[key] = number

    objects: List[Dict] = []

    for block in INSERT_BLOCK_PATTERN.findall(lua_text):
        entry: Dict[str, object] = {"type": "sensor"}
        unresolved = False
        name_hint = None

        for field in ("object", "instance", "resource"):
            field_pattern = re.compile(FIELD_PATTERN_TEMPLATE.format(field=field))
            fm = field_pattern.search(block)
            if not fm:
                unresolved = True
                break
            expr = fm.group(1).strip()
            number = _parse_numeric(expr)
            if number is None:
                number = constants.get(expr)
                if number is None:
                    unresolved = True
                    break
                if field == "resource":
                    name_hint = expr
            entry[field] = number

        if unresolved:
            continue

        entry["name"] = _title_from_var(name_hint) if name_hint else "Sensor"
        objects.append(entry)

    unique: List[Dict] = []
    seen = set()
    for item in objects:
        key = (item["object"], item["instance"], item["resource"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a wakaama-server sensor JSON file from a Lua script."
    )
    parser.add_argument(
        "--lua-file",
        required=True,
        help="Path to Lua script (e.g. lua/flow.lua)",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Sensor profile name (default: derived from file name)",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output JSON file path",
    )
    parser.add_argument(
        "--script-encoding",
        choices=["raw", "base64"],
        default="raw",
        help="How to store script in JSON (default: raw)",
    )
    parser.add_argument(
        "--fallback-object",
        action="append",
        default=[],
        metavar="OBJECT:INSTANCE:RESOURCE:NAME",
        help="Optional fallback object entry if auto-detection finds nothing. Can be set multiple times.",
    )
    args = parser.parse_args()

    lua_path = Path(args.lua_file)
    output_path = Path(args.output)

    lua_text = lua_path.read_text(encoding="utf-8")
    lwm2m_objects = extract_lwm2m_objects(lua_text)

    if not lwm2m_objects and args.fallback_object:
        for raw in args.fallback_object:
            try:
                object_id, instance_id, resource_id, name = raw.split(":", 3)
                lwm2m_objects.append(
                    {
                        "object": int(object_id),
                        "instance": int(instance_id),
                        "resource": int(resource_id),
                        "type": "sensor",
                        "name": name,
                    }
                )
            except ValueError as ex:
                raise SystemExit(
                    f"Invalid --fallback-object value '{raw}'. Expected OBJECT:INSTANCE:RESOURCE:NAME"
                ) from ex

    if not lwm2m_objects:
        raise SystemExit(
            "Could not auto-detect any lwm2m_objects from Lua script. "
            "Use --fallback-object OBJECT:INSTANCE:RESOURCE:NAME"
        )

    profile_name = args.name or lua_path.stem.replace("-", " ").replace("_", " ").title()

    if args.script_encoding == "base64":
        script_value = base64.b64encode(lua_text.encode("utf-8")).decode("ascii")
    else:
        script_value = lua_text

    payload = {
        "name": profile_name,
        "lwm2m_objects": lwm2m_objects,
        "script": script_value,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote {output_path}")
    print(f"Detected {len(lwm2m_objects)} lwm2m object(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
