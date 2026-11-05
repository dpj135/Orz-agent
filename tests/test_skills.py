import copy
import json
import os

import pytest

from mini_agent.loop import run_turn
from mini_agent.skills import MAX_FILE_BYTES, SkillCatalog
from mini_agent.tools import Policy, execute


def make_skill(root, folder="example", name="example", body="SECRET BODY"):
    path = root / ".agents" / "skills" / folder / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: Use for example tasks\n---\n{body}\n", encoding="utf-8"
    )
    return path


def test_empty_workspace(tmp_path):
    catalog = SkillCatalog.discover(tmp_path)
    assert catalog.context() == ""
    assert catalog.warnings == []


def test_summary_and_on_demand_body(tmp_path):
    make_skill(tmp_path)
    (tmp_path / "AGENTS.md").write_text("Project rules", encoding="utf-8")
    catalog = SkillCatalog.discover(tmp_path)
    assert "Project rules" in catalog.context()
    assert "example: Use for example tasks" in catalog.context()
    assert "SECRET BODY" not in catalog.context()
    result = execute("skill", {"name": "example"}, Policy(tmp_path), lambda _: False, catalog)
    assert result["instructions"] == "SECRET BODY"
    assert result["base_directory"] == ".agents/skills/example"


@pytest.mark.parametrize("name", ["../secret", "unknown", "", 123, ["example"]])
def test_unknown_name_fails_closed(tmp_path, name):
    make_skill(tmp_path)
    result = execute(
        "skill", {"name": name}, Policy(tmp_path), lambda _: False, SkillCatalog.discover(tmp_path)
    )
    assert not result["ok"]


def test_extra_arguments_rejected(tmp_path):
    result = execute(
        "skill",
        {"name": "example", "path": "secret"},
        Policy(tmp_path),
        lambda _: False,
        SkillCatalog.discover(tmp_path),
    )
    assert not result["ok"]


@pytest.mark.parametrize(
    "text",
    [
        "no front matter",
        "---\nname: bad\n",
        "---\nname: example\ndescription: []\n---\nbody",
        "---\nname: Bad_Name\ndescription: x\n---\nbody",
        "---\nname: example\ndescription: x\n---\n",
    ],
)
def test_invalid_metadata_skipped(tmp_path, text):
    path = make_skill(tmp_path)
    path.write_text(text, encoding="utf-8")
    catalog = SkillCatalog.discover(tmp_path)
    assert not catalog.entries
    assert catalog.warnings


def test_duplicate_names_all_rejected(tmp_path):
    make_skill(tmp_path, "first")
    make_skill(tmp_path, "second")
    catalog = SkillCatalog.discover(tmp_path)
    assert not catalog.entries
    assert any("Duplicate" in message for message in catalog.warnings)


def test_oversized_files_rejected(tmp_path):
    make_skill(tmp_path, body="x" * MAX_FILE_BYTES)
    (tmp_path / "AGENTS.md").write_text("x" * (MAX_FILE_BYTES + 1), encoding="utf-8")
    catalog = SkillCatalog.discover(tmp_path)
    assert not catalog.entries
    assert not catalog.instructions
    assert len(catalog.warnings) == 2


def test_revalidate_on_load(tmp_path):
    path = make_skill(tmp_path)
    catalog = SkillCatalog.discover(tmp_path)
    path.write_text("x" * (MAX_FILE_BYTES + 1), encoding="utf-8")
    with pytest.raises(ValueError):
        catalog.load("example")


def test_metadata_changes_need_rescan(tmp_path):
    path = make_skill(tmp_path)
    catalog = SkillCatalog.discover(tmp_path)
    path.write_text(path.read_text().replace("Use for example tasks", "Changed"), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        catalog.load("example")


def test_symlink_rejected(tmp_path):
    path = make_skill(tmp_path)
    target = tmp_path / "target.md"
    path.rename(target)
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("OS does not permit symlink creation")
    catalog = SkillCatalog.discover(tmp_path)
    assert not catalog.entries
    assert catalog.warnings


def test_windows_junction_root_rejected(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction test")
    import subprocess

    target = tmp_path / "target"
    make_skill(target)
    agents = tmp_path / ".agents"
    agents.mkdir()
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(agents / "skills"), str(target / ".agents" / "skills")],
        capture_output=True,
    )
    if result.returncode:
        pytest.skip("junction creation unavailable")
    catalog = SkillCatalog.discover(tmp_path)
    assert not catalog.entries
    assert catalog.warnings


def test_loop_skill_load_then_answer(tmp_path):
    make_skill(tmp_path)

    class FakeModel:
        def __init__(self):
            self.requests = []

        def complete(self, messages, tools):
            self.requests.append(copy.deepcopy(messages))
            assert any(tool["function"]["name"] == "skill" for tool in tools)
            if len(self.requests) == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "skill-1",
                                        "type": "function",
                                        "function": {
                                            "name": "skill",
                                            "arguments": '{"name":"example"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

    client = FakeModel()
    policy = Policy(tmp_path)
    assert run_turn("example task", client, policy, on_event=lambda _: None) == "done"
    assert "SECRET BODY" not in json.dumps(client.requests[0])
    assert "SECRET BODY" in json.dumps(client.requests[1])
    assert client.requests[1][-1]["tool_call_id"] == "skill-1"
    assert not policy.allow_write and not policy.allow_command
