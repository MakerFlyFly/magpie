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
"""Exercise CLI startup against proxy.py without opening listening sockets."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

import proxy
import pytest

from egress_gateway import cli
from egress_gateway.allowlist import EgressAllowlistPlugin


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, proxy.Proxy]]:
    events: list[tuple[str, proxy.Proxy]] = []
    monkeypatch.setattr(proxy.Proxy, "setup", lambda gateway: events.append(("setup", gateway)))
    monkeypatch.setattr(proxy.Proxy, "shutdown", lambda gateway: events.append(("shutdown", gateway)))
    monkeypatch.setattr(proxy, "sleep_loop", lambda gateway: events.append(("wait", gateway)))
    return events


def test_main_starts_proxy_with_defaults(tmp_path: Path, lifecycle: list[tuple[str, proxy.Proxy]]) -> None:
    assert cli.main(["--data-dir", str(tmp_path)]) == 0

    gateway = lifecycle[0][1]
    assert lifecycle == [("setup", gateway), ("wait", gateway), ("shutdown", gateway)]
    assert str(gateway.flags.hostname) == "127.0.0.1"
    assert gateway.flags.port == 8899
    assert gateway.flags.log_level == "INFO"
    assert gateway.flags.plugins[b"HttpProxyBasePlugin"] == [EgressAllowlistPlugin]


@pytest.mark.parametrize("explicit_argv", [True, False])
def test_main_forwards_custom_and_passthrough_arguments(
    explicit_argv: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle: list[tuple[str, proxy.Proxy]],
) -> None:
    argv = [
        "--host",
        "127.0.0.2",
        "--port",
        "9001",
        "--log-level",
        "DEBUG",
        "--num-workers",
        "1",
        "--num-acceptors",
        "2",
        "--data-dir",
        str(tmp_path),
    ]
    process_argv = ["caller", "--port", "1"] if explicit_argv else ["egress-gateway", *argv]
    monkeypatch.setattr(sys, "argv", process_argv)

    assert cli.main(argv if explicit_argv else None) == 0

    gateway = lifecycle[0][1]
    assert str(gateway.flags.hostname) == "127.0.0.2"
    assert gateway.flags.port == 9001
    assert gateway.flags.log_level == "DEBUG"
    assert gateway.flags.num_workers == 1
    assert gateway.flags.num_acceptors == 2
    assert gateway.flags.proxy_py_data_dir == str(tmp_path)
    assert gateway.flags.plugins[b"HttpProxyBasePlugin"] == [EgressAllowlistPlugin]
    assert sys.argv is process_argv


def test_main_shuts_down_when_waiting_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle: list[tuple[str, proxy.Proxy]],
) -> None:
    def fail_wait(gateway: proxy.Proxy) -> None:
        lifecycle.append(("wait", gateway))
        raise RuntimeError("wait failed")

    monkeypatch.setattr(proxy, "sleep_loop", fail_wait)

    with pytest.raises(RuntimeError, match="wait failed"):
        cli.main(["--data-dir", str(tmp_path)])

    gateway = lifecycle[0][1]
    assert lifecycle == [("setup", gateway), ("wait", gateway), ("shutdown", gateway)]


def test_help_does_not_import_proxy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_import = builtins.__import__

    def without_proxy(name: str, *args: object, **kwargs: object) -> object:
        if name == "proxy" or name.startswith("proxy."):
            raise ModuleNotFoundError("proxy.py is unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_proxy)

    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0
    assert "--port" in capsys.readouterr().out
