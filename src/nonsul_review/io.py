"""Bounded, strict UTF-8 inputs and atomic non-overwriting output files."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .schema import Answer, Problem

MAX_FILE_BYTES = 8 * 1024 * 1024


class InputError(ValueError):
    """Invalid input, reported without echoing private contents."""


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _mapping(loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            exists = key in mapping
        except TypeError:
            raise InputError("YAML mapping keys must be scalar values") from None
        if exists:
            raise InputError("duplicate YAML mapping key")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def read_text(path: Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise InputError(f"file not found: {path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise InputError(f"input exceeds {MAX_FILE_BYTES} bytes: {path.name}")
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeError:
        raise InputError(f"input must be UTF-8: {path.name}") from None


def parse_yaml(text: str) -> Any:
    try:
        # Aliases make cycles/expansion possible and are unnecessary in data files.
        for event in yaml.parse(text):
            if isinstance(event, yaml.events.AliasEvent):
                raise InputError("YAML aliases are not supported")
        return yaml.load(text, Loader=UniqueKeyLoader)
    except (yaml.YAMLError, RecursionError):
        raise InputError("invalid YAML; check indentation and quoting") from None


def read_yaml(path: Path) -> Any:
    return parse_yaml(read_text(path))


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in pairs:
        if key in data:
            raise InputError("duplicate JSON object key")
        data[key] = value
    return data


def _invalid_constant(value: str) -> None:
    raise InputError("non-finite JSON numbers are not supported")


def read_json(path: Path) -> Any:
    try:
        return json.loads(
            read_text(path), object_pairs_hook=_json_pairs, parse_constant=_invalid_constant
        )
    except (json.JSONDecodeError, RecursionError):
        raise InputError(f"invalid JSON: {Path(path).name}") from None


def canonical_hash(data: Any) -> str:
    encoded = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def schema_error(exc: ValidationError) -> InputError:
    # Never include pydantic's input_value: it can contain a complete learner answer.
    fields = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False)[:8]:
        fields.append(".".join(map(str, error["loc"])) or "document")
    return InputError("schema validation failed at: " + ", ".join(fields))


def load_problem(path: Path) -> Problem:
    try:
        return Problem.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise schema_error(exc) from None


def load_answer(path: Path) -> Answer:
    text = read_text(path)
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise InputError(f"answer must start with YAML front matter: {Path(path).name}")
    closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if closing is None:
        raise InputError(f"answer front matter has no closing delimiter: {Path(path).name}")
    metadata = parse_yaml("".join(lines[1:closing]))
    if not isinstance(metadata, dict):
        raise InputError("answer front matter must be a mapping")
    if "body" in metadata:
        raise InputError("body must be after the front matter, not inside metadata")
    body = "".join(lines[closing + 1 :]).strip()
    try:
        return Answer.model_validate({**metadata, "body": body})
    except ValidationError as exc:
        raise schema_error(exc) from None


def _atomic_write(path: Path, content: str, *, overwrite: bool = False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {path}")
    if path.is_symlink():
        raise InputError("refusing to write through a symbolic link")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(tmp, path)
        else:
            # A hard link publishes a complete file atomically and fails if it exists.
            os.link(tmp, path)
            tmp.unlink()
    finally:
        tmp.unlink(missing_ok=True)
    return path


def write_json(path: Path, data: Any, *, overwrite: bool = False) -> Path:
    return _atomic_write(
        path,
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        overwrite=overwrite,
    )


def write_yaml(path: Path, data: Any, *, overwrite: bool = False) -> Path:
    return _atomic_write(
        path,
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100),
        overwrite=overwrite,
    )
