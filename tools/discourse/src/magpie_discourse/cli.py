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

"""Command line interface for the Discourse bridge."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from magpie_discourse import topics
from magpie_discourse.client import load_config


def main(argv: Sequence[str] | None = None) -> int:
    """Run the magpie-discourse command line interface."""
    args = _build_parser().parse_args(argv)
    config = load_config()

    result: Any
    if args.subcommand == "topic" and args.topic_action == "view":
        topic_id = topics.parse_topic_ref(args.topic, config)
        result = topics.fetch_topic(config, topic_id, max_posts=args.max_posts)
    else:  # pragma: no cover - argparse rejects anything else
        raise AssertionError(f"unhandled command {args}")

    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="magpie-discourse",
        description="Discourse forum bridge for Apache Magpie (read-only).",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    topic_parser = subparsers.add_parser("topic", help="Read forum topics.")
    topic_subparsers = topic_parser.add_subparsers(dest="topic_action", required=True)
    view = topic_subparsers.add_parser("view", help="Fetch one topic with its posts.")
    view.add_argument("topic", help="Topic id or topic URL on the configured forum.")
    view.add_argument(
        "--max-posts",
        type=int,
        default=topics.DEFAULT_MAX_POSTS,
        help=f"Read at most this many posts (default {topics.DEFAULT_MAX_POSTS}); "
        "a longer topic is returned with truncated: true.",
    )
    return parser
