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

"""Exercise the actual tests-ok shell gate, including discovery failures."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

_WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/tests.yml"
_RESULTS = ("success", "failure", "cancelled", "skipped")


@pytest.fixture
def gate_job() -> str:
    # Read only this job's literal block; no YAML dependency is needed.
    match = re.search(r"(?ms)^  tests-ok:\n(.*?)(?=^  \S|\Z)", _WORKFLOW.read_text(encoding="utf-8"))
    assert match is not None, "tests-ok job is missing"
    return match[1]


@pytest.fixture
def bash() -> str:
    executable = shutil.which("bash")
    if executable is None:
        pytest.skip("the workflow gate needs Bash")
    return executable


def test_gate_waits_for_both_jobs_and_runs_after_failure(gate_job: str) -> None:
    needs = re.search(r"(?m)^    needs: (.+)$", gate_job)
    assert needs is not None
    assert {job.strip() for job in needs[1].strip("[]").split(",")} == {"members", "pytest"}
    assert re.search(r"(?m)^    if: always\(\)$", gate_job)


@pytest.mark.parametrize("members_result", _RESULTS)
@pytest.mark.parametrize("pytest_result", _RESULTS)
def test_gate_results(gate_job: str, bash: str, members_result: str, pytest_result: str) -> None:
    run = re.search(r"(?m)^        run: \|\n((?:          .*\n|\n)+)", gate_job)
    assert run is not None, "tests-ok shell assertion is missing"
    script = dedent(run[1])
    for job, result in (("members", members_result), ("pytest", pytest_result)):
        script = script.replace("${{ needs." + job + ".result }}", result)
    assert "${{" not in script, "unresolved Actions expression in the gate"
    completed = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    passing = {("success", "success"), ("success", "skipped")}
    assert (completed.returncode == 0) == ((members_result, pytest_result) in passing), (
        f"members={members_result}, pytest={pytest_result}, exit={completed.returncode}\n"
        f"{completed.stdout}{completed.stderr}"
    )
