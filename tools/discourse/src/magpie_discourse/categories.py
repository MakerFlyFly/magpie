# SPDX-License-Identifier: Apache-2.0
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Resolve category references without using a slug in an HTTP request path."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from magpie_discourse.client import DiscourseConfig, DiscourseError, get_json


def _slug(value: str) -> str:
    """Decode one slug component, refusing ambiguous or nested escapes."""
    if re.search(r"%(?![0-9a-fA-F]{2})|%(?:2f|5c)", value, re.IGNORECASE):
        raise DiscourseError("Invalid category slug encoding")
    try:
        decoded = unquote(value, errors="strict")
    except UnicodeDecodeError as exc:
        raise DiscourseError("Invalid category slug encoding") from exc
    if (
        not decoded
        or decoded in (".", "..")
        or any(char in decoded for char in "/\\%?#")
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
    ):
        raise DiscourseError("Invalid category slug or path")
    return decoded


def _category(raw: Any) -> dict[str, Any]:
    """Validate the metadata required for a category selection."""
    if (
        not isinstance(raw, dict)
        or type(raw.get("id")) is not int
        or raw["id"] < 1
        or not isinstance(raw.get("slug"), str)
        or not isinstance(raw.get("name"), str)
    ):
        raise DiscourseError("Discourse returned invalid category metadata")
    _slug(raw["slug"])
    return {key: raw[key] for key in ("id", "slug", "name")}


def resolve_category(config: DiscourseConfig, ref: str) -> dict[str, Any]:
    """Resolve an ID, unique catalog slug or full catalog slug path."""
    value = ref.strip()
    if value.startswith(("+", "-")) and value[1:].isdigit():
        raise DiscourseError("Category id must be a positive integer using ASCII digits")
    if value.isdigit():
        if not re.fullmatch(r"[0-9]+", value):
            raise DiscourseError("Category id must use ASCII digits")
        try:
            category_id = int(value)
        except ValueError as exc:
            raise DiscourseError("Invalid category id") from exc
        if category_id < 1:
            raise DiscourseError("Category id must be a positive integer")
        category = _category(get_json(config, f"/c/{category_id}/show.json").get("category"))
        if category["id"] != category_id:
            raise DiscourseError("Discourse returned a different category id")
        return category

    wanted = tuple(_slug(part) for part in value.split("/"))
    payload = get_json(config, "/categories.json", {"include_subcategories": "true"})
    envelope = payload.get("category_list")
    if not isinstance(envelope, dict) or not isinstance(envelope.get("categories"), list):
        raise DiscourseError("Discourse returned an invalid category collection")
    nodes: dict[int, tuple[dict[str, Any], int | None]] = {}

    def visit(items: list[Any], parent: int | None = None) -> None:
        for raw in items:
            category = _category(raw)
            parent_id = raw.get("parent_category_id", parent)
            if parent_id is not None and (type(parent_id) is not int or parent_id < 1):
                raise DiscourseError("Discourse returned an invalid parent category id")
            if parent is not None and parent_id != parent:
                raise DiscourseError("Discourse returned inconsistent category ancestry")
            entry = (category, parent_id)
            category_id = category["id"]
            if category_id in nodes and nodes[category_id] != entry:
                raise DiscourseError("Discourse returned conflicting category metadata")
            nodes[category_id] = entry
            children = raw.get("subcategory_list", [])
            if not isinstance(children, list):
                raise DiscourseError("Discourse returned an invalid subcategory collection")
            visit(children, category_id)

    visit(envelope["categories"])

    def path_for(category_id: int, ancestors: frozenset[int] = frozenset()) -> tuple[str, ...] | None:
        if category_id in ancestors:
            raise DiscourseError("Discourse returned cyclic category ancestry")
        category, parent_id = nodes[category_id]
        own = (_slug(category["slug"]),)
        if parent_id is None:
            return own
        if parent_id not in nodes:
            return None  # A partial catalog cannot establish this category's full path.
        parent_path = path_for(parent_id, ancestors | {category_id})
        return parent_path + own if parent_path is not None else None

    matches = []
    for category_id, (category, _) in nodes.items():
        path = path_for(category_id)
        if (len(wanted) == 1 and _slug(category["slug"]) == wanted[0]) or path == wanted:
            matches.append(category)
    if len(matches) > 1:
        raise DiscourseError("Category slug is ambiguous; use a full parent/child path or numeric id")
    if not matches:
        raise DiscourseError("Cannot resolve category from the returned catalog; try a numeric category id")
    return matches[0]
