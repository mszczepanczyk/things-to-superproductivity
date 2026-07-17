"""Tests for things_to_superproductivity.export.build_export.

Uses the sample Things database from things.py's own test suite
(https://github.com/thingsapi/things.py/tree/main/tests), copied into
tests/fixtures/ since it isn't shipped in the installed things.py
package (its sdist/wheel only contain the `things` module, not the
`tests/` directory). Expected counts are computed independently via
`things` itself in each test rather than hardcoded, so the tests stay
correct even if the upstream fixture changes.
"""

import plistlib
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import things
import pytest

from things_to_superproductivity.export import (
    INBOX_PROJECT_ID,
    WEEKDAY_NAMES,
    assert_status,
    build_export,
    decode_recurrence_rule,
)

FIXTURE_DB = str(Path(__file__).parent / "fixtures" / "main.sqlite")


@pytest.fixture
def backup_and_stats():
    return build_export(db_path=FIXTURE_DB)


def _find_task(backup, uuid):
    """Look up a task by id in either the live or the archived task state."""
    data = backup["data"]
    return data["task"]["entities"].get(uuid) or data["archiveYoung"]["task"][
        "entities"
    ].get(uuid)


def test_referential_integrity(backup_and_stats):
    """Every id referenced by a task/project/tag must point at a real entity."""
    backup, _ = backup_and_stats
    data = backup["data"]

    task_ids = set(data["task"]["ids"])
    task_entities = data["task"]["entities"]
    assert task_ids == set(task_entities.keys())

    project_ids = set(data["project"]["ids"])
    project_entities = data["project"]["entities"]
    assert project_ids == set(project_entities.keys())

    tag_ids = set(data["tag"]["ids"])
    tag_entities = data["tag"]["entities"]
    assert tag_ids == set(tag_entities.keys())

    archive_task_ids = set(data["archiveYoung"]["task"]["ids"])
    archive_task_entities = data["archiveYoung"]["task"]["entities"]
    assert archive_task_ids == set(archive_task_entities.keys())
    # live and archived task ids must never overlap
    assert not (task_ids & archive_task_ids)

    all_entities = {**task_entities, **archive_task_entities}
    for task in all_entities.values():
        assert task["projectId"] in project_ids
        for tag_id in task["tagIds"]:
            assert tag_id in tag_ids

    # sub-tasks: parent must exist (live or archived) and list the child
    # back in its subTaskIds; a subtask lives in the same state as its parent
    for task_id, task in all_entities.items():
        parent_id = task.get("parentId")
        if parent_id:
            assert parent_id in all_entities
            assert task_id in all_entities[parent_id]["subTaskIds"]
            same_state = (task_id in task_entities) == (parent_id in task_entities)
            assert same_state

    # a project's/tag's taskIds may only reference *live* top-level tasks
    # (archived tasks are removed from these lists, as Super Productivity
    # itself does when a task is archived)
    for project in project_entities.values():
        for task_id in project["taskIds"]:
            assert task_id in task_entities
            assert not task_entities[task_id].get("parentId")
    for tag in tag_entities.values():
        for task_id in tag["taskIds"]:
            assert task_id in task_entities


def test_backup_wrapper_shape(backup_and_stats):
    backup, _ = backup_and_stats
    assert set(backup.keys()) == {
        "timestamp",
        "lastUpdate",
        "crossModelVersion",
        "data",
    }
    assert set(backup["data"].keys()) == {
        "project",
        "menuTree",
        "globalConfig",
        "task",
        "tag",
        "simpleCounter",
        "taskRepeatCfg",
        "reminders",
        "note",
        "metric",
        "planner",
        "issueProvider",
        "boards",
        "timeTracking",
        "archiveYoung",
        "archiveOld",
        "pluginMetadata",
        "pluginUserData",
    }


def test_inbox_project_always_present(backup_and_stats):
    backup, _ = backup_and_stats
    assert INBOX_PROJECT_ID in backup["data"]["project"]["entities"]


def test_project_count_matches_things(backup_and_stats):
    backup, stats = backup_and_stats
    expected_projects = things.projects(status=None, filepath=FIXTURE_DB)
    assert stats["projects"] == len(expected_projects)
    # +1 for the always-present Inbox project
    assert len(backup["data"]["project"]["ids"]) == len(expected_projects) + 1


def _expected_special_tag_count(area_tags, someday_tags, anytime_tags):
    """Extra tags beyond Things' own, computed independently from the
    fixture: one per area (if area_tags), one for Someday/Anytime each (if
    enabled and actually used), and one for Canceled whenever the fixture
    has any canceled to-do (always on, not configurable)."""
    todos = things.todos(status=None, filepath=FIXTURE_DB)
    starts = {t["start"] for t in todos}
    count = 0
    if area_tags:
        count += len(things.areas(filepath=FIXTURE_DB))
    if someday_tags and "Someday" in starts:
        count += 1
    if anytime_tags and "Anytime" in starts:
        count += 1
    if any(t["status"] == "canceled" for t in todos):
        count += 1
    return count


def test_tag_count_matches_things(backup_and_stats):
    backup, stats = backup_and_stats
    expected_tags = things.tags(filepath=FIXTURE_DB)
    expected_areas = things.areas(filepath=FIXTURE_DB)
    assert stats["tags"] == len(expected_tags)
    assert stats["areas"] == len(expected_areas)
    assert len(backup["data"]["tag"]["ids"]) == len(
        expected_tags
    ) + _expected_special_tag_count(
        area_tags=True, someday_tags=True, anytime_tags=False
    )


def test_area_tags_can_be_disabled():
    backup, stats = build_export(db_path=FIXTURE_DB, area_tags=False)
    expected_tags = things.tags(filepath=FIXTURE_DB)
    assert len(backup["data"]["tag"]["ids"]) == len(
        expected_tags
    ) + _expected_special_tag_count(
        area_tags=False, someday_tags=True, anytime_tags=False
    )


def test_someday_tag_by_default_anytime_not(backup_and_stats):
    """Someday is tagged by default (a meaningful, relatively rare
    distinction); Anytime is not (the common/default state for most
    to-dos, which would just add noise). Inbox gets no tag either way,
    since those to-dos already land in Super Productivity's Inbox
    project."""
    backup, _ = backup_and_stats
    tag_id_by_title = {
        t["title"]: tid for tid, t in backup["data"]["tag"]["entities"].items()
    }
    someday_tag_id = tag_id_by_title["Someday"]
    assert "Anytime" not in tag_id_by_title
    assert "Inbox" not in tag_id_by_title

    todos = things.todos(status=None, filepath=FIXTURE_DB)
    by_start = {"Someday": 0, "Anytime": 0, "Inbox": 0}
    for todo in todos:
        by_start[todo["start"]] += 1
        task = _find_task(backup, todo["uuid"])
        if todo["start"] == "Someday":
            assert someday_tag_id in task["tagIds"]

    # sanity check the fixture actually exercises all three start locations
    assert all(count > 0 for count in by_start.values())


def test_anytime_tags_opt_in():
    backup, _ = build_export(db_path=FIXTURE_DB, anytime_tags=True)
    tag_id_by_title = {
        t["title"]: tid for tid, t in backup["data"]["tag"]["entities"].items()
    }
    anytime_tag_id = tag_id_by_title["Anytime"]

    todos = things.todos(status=None, filepath=FIXTURE_DB)
    anytime_todos = [t for t in todos if t["start"] == "Anytime"]
    assert len(anytime_todos) > 0
    for todo in anytime_todos:
        task = _find_task(backup, todo["uuid"])
        assert anytime_tag_id in task["tagIds"]


def test_someday_tags_can_be_disabled():
    backup, _ = build_export(db_path=FIXTURE_DB, someday_tags=False)
    tag_titles = {t["title"] for t in backup["data"]["tag"]["entities"].values()}
    assert "Someday" not in tag_titles


def test_canceled_todos_are_done_and_tagged(backup_and_stats):
    """Canceled to-dos are always included (not skipped), marked done, and
    tagged 'Canceled' so they stay distinguishable from genuinely completed
    to-dos."""
    backup, _ = backup_and_stats
    expected_canceled = things.todos(status="canceled", filepath=FIXTURE_DB)
    assert len(expected_canceled) > 0

    tag_id_by_title = {
        t["title"]: tid for tid, t in backup["data"]["tag"]["entities"].items()
    }
    canceled_tag_id = tag_id_by_title["Canceled"]

    for todo in expected_canceled:
        task = _find_task(backup, todo["uuid"])
        assert task["isDone"] is True
        assert canceled_tag_id in task["tagIds"]


def test_checklist_items_become_subtasks(backup_and_stats):
    _, stats = backup_and_stats
    todos = things.todos(status=None, include_items=True, filepath=FIXTURE_DB)
    expected_subtasks = sum(len(todo.get("checklist") or []) for todo in todos)
    assert stats["subtasks"] + stats["archived_subtasks"] == expected_subtasks


def test_dates_are_mapped_for_open_tasks(backup_and_stats):
    """Things start/when date -> dueDay; Things deadline -> deadlineDay."""
    backup, _ = backup_and_stats
    task_entities = backup["data"]["task"]["entities"]
    todos = things.todos(status=None, filepath=FIXTURE_DB)
    todos_by_uuid = {t["uuid"]: t for t in todos}

    checked_due = checked_deadline = 0
    for uuid, task in task_entities.items():
        todo = todos_by_uuid.get(uuid)
        if todo is None or task["isDone"]:
            continue  # a sub-task (checklist item) or a done task
        if todo.get("start_date"):
            assert task["dueDay"] == todo["start_date"]
            checked_due += 1
        if todo.get("deadline"):
            assert task["deadlineDay"] == todo["deadline"]
            checked_deadline += 1

    # sanity check the fixture actually exercises both fields
    assert checked_due > 0
    assert checked_deadline > 0


def test_dates_dropped_for_done_tasks_by_default(backup_and_stats):
    """A due/deadline date on an already-done task would otherwise flood
    Super Productivity's Today view, since its overdue selector doesn't
    exclude done tasks (see selectOverdueTasks in task.selectors.ts)."""
    backup, _ = backup_and_stats
    todos = things.todos(status="completed", filepath=FIXTURE_DB)

    checked = 0
    for todo in todos:
        if todo.get("start_date") or todo.get("deadline"):
            task = _find_task(backup, todo["uuid"])
            assert "dueDay" not in task
            assert "deadlineDay" not in task
            checked += 1

    # sanity check the fixture actually exercises this case
    assert checked > 0


def test_keep_dates_on_done_flag():
    backup, _ = build_export(db_path=FIXTURE_DB, dates_on_done=True)
    todos = things.todos(status="completed", filepath=FIXTURE_DB)

    checked = 0
    for todo in todos:
        task = _find_task(backup, todo["uuid"])
        if todo.get("start_date"):
            assert task["dueDay"] == todo["start_date"]
            checked += 1
        if todo.get("deadline"):
            assert task["deadlineDay"] == todo["deadline"]
            checked += 1

    assert checked > 0


def test_done_tasks_are_archived_by_default(backup_and_stats):
    """The actual fix: Super Productivity's Today view aggregates every done
    task in the *live* state app-wide with no date filter (its doneTasks$
    switches to selectAllTasksWithSubTasks for the Today context). Done
    tasks must be moved to the archive - removed from the live task state
    and from every project's/tag's taskIds - exactly like Super
    Productivity's own archiving (handleMoveToArchive) does."""
    backup, stats = backup_and_stats
    data = backup["data"]
    live_task_entities = data["task"]["entities"]
    archive_task_entities = data["archiveYoung"]["task"]["entities"]

    expected_done = things.todos(status="completed", filepath=FIXTURE_DB)
    assert len(expected_done) > 0
    assert stats["archived_tasks"] >= len(expected_done)

    for todo in expected_done:
        uuid = todo["uuid"]
        assert uuid not in live_task_entities
        archived = archive_task_entities[uuid]
        assert archived["isDone"] is True
        assert isinstance(archived["doneOn"], int)

    live_task_ids = set(data["task"]["ids"])
    for project in data["project"]["entities"].values():
        assert not (set(project["taskIds"]) - live_task_ids)
    for tag in data["tag"]["entities"].values():
        assert not (set(tag["taskIds"]) - live_task_ids)


def test_archive_done_can_be_disabled():
    backup, stats = build_export(db_path=FIXTURE_DB, archive_done=False)
    assert stats["archived_tasks"] == 0
    assert stats["archived_subtasks"] == 0
    assert backup["data"]["archiveYoung"]["task"]["ids"] == []

    expected_done = things.todos(status="completed", filepath=FIXTURE_DB)
    live_task_entities = backup["data"]["task"]["entities"]
    for todo in expected_done:
        task = live_task_entities[todo["uuid"]]
        assert task["isDone"] is True
        assert task["id"] in backup["data"]["project"]["entities"][
            task["projectId"]
        ]["taskIds"]


def test_heading_prefixes_task_title(backup_and_stats):
    """Super Productivity has no heading container, so a to-do filed under
    a Things heading gets its heading folded into the title instead."""
    backup, _ = backup_and_stats
    todos = things.todos(status=None, filepath=FIXTURE_DB)
    headed_todos = [t for t in todos if t.get("heading")]

    # sanity check the fixture actually exercises this case
    assert len(headed_todos) > 0

    for todo in headed_todos:
        task = _find_task(backup, todo["uuid"])
        assert task["title"] == f"{todo['heading_title']} > {todo['title']}"


def test_assert_status_rejects_unexpected_value():
    with pytest.raises(ValueError):
        assert_status("bogus", "some-uuid")


def _copy_fixture_with_project_notes(tmp_path, project_uuid, notes_text):
    """Copy the fixture DB and inject notes onto one project, so the note-
    to-Note-entity mapping can be exercised against real Things schema
    without mutating the checked-in fixture (no project in it has notes)."""
    dest_dir = tmp_path / "db"
    dest_dir.mkdir()
    fixtures_dir = Path(FIXTURE_DB).parent
    for name in ("main.sqlite", "main.sqlite-wal", "main.sqlite-shm"):
        shutil.copy(fixtures_dir / name, dest_dir / name)
    dest_db = dest_dir / "main.sqlite"

    conn = sqlite3.connect(dest_db)
    conn.execute(
        "UPDATE TMTask SET notes = ? WHERE uuid = ?", (notes_text, project_uuid)
    )
    conn.commit()
    conn.close()
    return str(dest_db)


def test_project_notes_become_note_entities(tmp_path):
    """Super Productivity projects hold notes via noteIds referencing
    separate Note entities, not a plain string field, so a Things
    project's notes must be written there to actually be kept."""
    project = things.projects(status=None, filepath=FIXTURE_DB)[0]
    notes_text = "Some project notes to preserve"
    db_path = _copy_fixture_with_project_notes(tmp_path, project["uuid"], notes_text)

    backup, _ = build_export(db_path=db_path)
    data = backup["data"]

    project_entity = data["project"]["entities"][project["uuid"]]
    assert len(project_entity["noteIds"]) == 1
    note_id = project_entity["noteIds"][0]

    assert note_id in data["note"]["ids"]
    note = data["note"]["entities"][note_id]
    assert note["content"] == notes_text
    assert note["projectId"] == project["uuid"]


# --- recurring to-dos ---
#
# Things' rt1_recurrenceRule is an undocumented binary plist; the fixture
# has exactly one real recurring to-do ("Repeating To-Do", weekly on
# Sunday). To exercise the other shapes (daily/monthly/yearly/multi-
# weekday) this mapping supports, these tests inject synthetic
# rt1_recurrenceRule blobs onto an existing plain to-do in a copied DB,
# the same way test_project_notes_become_note_entities injects notes.

# "To-Do in Inbox": a plain incomplete to-do in the fixture with no
# existing recurrence rule, repurposed below as a synthetic template.
PLAIN_TODO_UUID = "DfYoiXcNLQssk9DkSoJV3Y"


def _epoch_utc_midnight(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


FAR_FUTURE_END_DATE = _epoch_utc_midnight("4001-01-01")


def _copy_fixture_with_recurrence(tmp_path, uuid, rule_overrides, is_paused=0):
    """Copy the fixture DB and turn an existing plain to-do into a
    synthetic recurring template by injecting a specific
    rt1_recurrenceRule plist, without mutating the checked-in fixture."""
    dest_dir = tmp_path / "db"
    dest_dir.mkdir()
    fixtures_dir = Path(FIXTURE_DB).parent
    for name in ("main.sqlite", "main.sqlite-wal", "main.sqlite-shm"):
        shutil.copy(fixtures_dir / name, dest_dir / name)
    dest_db = dest_dir / "main.sqlite"

    rule = {
        "ed": FAR_FUTURE_END_DATE,
        "fa": 1,
        "rc": 0,
        "rrv": 4,
        "sr": _epoch_utc_midnight("2024-01-01"),
        "ts": 0,
        **rule_overrides,
    }
    blob = plistlib.dumps(rule)

    conn = sqlite3.connect(dest_db)
    conn.execute(
        "UPDATE TMTask SET rt1_recurrenceRule = ?, rt1_instanceCreationPaused = ? "
        "WHERE uuid = ?",
        (blob, is_paused, uuid),
    )
    conn.commit()
    conn.close()
    return str(dest_db)


def test_recurring_todo_linked_to_repeat_cfg(backup_and_stats):
    """The fixture's one real recurring to-do should get a taskRepeatCfg
    entity, and its live instance should reference it via repeatCfgId."""
    backup, stats = backup_and_stats
    assert stats["recurring_configs"] == 1
    cfg_id, cfg = next(iter(backup["data"]["taskRepeatCfg"]["entities"].items()))
    assert cfg["title"] == "Repeating To-Do"
    assert cfg["repeatCycle"] == "WEEKLY"
    assert cfg["sunday"] is True
    assert cfg["repeatEvery"] == 1

    linked = [
        t
        for t in backup["data"]["task"]["entities"].values()
        if t.get("repeatCfgId") == cfg_id
    ]
    assert len(linked) == 1
    assert linked[0]["title"] == "Repeating To-Do"


def test_daily_recurrence(tmp_path):
    db_path = _copy_fixture_with_recurrence(
        tmp_path,
        PLAIN_TODO_UUID,
        {"fu": 16, "tp": 0, "of": [{"dy": 0}], "ia": _epoch_utc_midnight("2024-03-05")},
    )
    backup, stats = build_export(db_path=db_path)
    assert stats["recurring_configs"] == 2  # the fixture's own + this one
    cfg = backup["data"]["taskRepeatCfg"]["entities"][PLAIN_TODO_UUID]
    assert cfg["repeatCycle"] == "DAILY"
    assert cfg["startDate"] == "2024-03-05"
    assert not any(cfg[day] for day in WEEKDAY_NAMES)


def test_monthly_last_day_recurrence(tmp_path):
    db_path = _copy_fixture_with_recurrence(
        tmp_path,
        PLAIN_TODO_UUID,
        {"fu": 8, "tp": 0, "of": [{"dy": -1}], "ia": _epoch_utc_midnight("2024-02-29")},
    )
    backup, _ = build_export(db_path=db_path)
    cfg = backup["data"]["taskRepeatCfg"]["entities"][PLAIN_TODO_UUID]
    assert cfg["repeatCycle"] == "MONTHLY"
    assert cfg["monthlyLastDay"] is True


def test_monthly_day_of_month_recurrence(tmp_path):
    db_path = _copy_fixture_with_recurrence(
        tmp_path,
        PLAIN_TODO_UUID,
        {"fu": 8, "tp": 0, "of": [{"dy": 14}], "ia": _epoch_utc_midnight("2024-06-15")},
    )
    backup, _ = build_export(db_path=db_path)
    cfg = backup["data"]["taskRepeatCfg"]["entities"][PLAIN_TODO_UUID]
    assert cfg["repeatCycle"] == "MONTHLY"
    assert cfg["startDate"] == "2024-06-15"
    assert "monthlyLastDay" not in cfg


def test_yearly_recurrence(tmp_path):
    db_path = _copy_fixture_with_recurrence(
        tmp_path,
        PLAIN_TODO_UUID,
        {
            "fu": 4,
            "tp": 0,
            "of": [{"dy": 19, "mo": 10}],
            "ia": _epoch_utc_midnight("2020-11-20"),
        },
    )
    backup, _ = build_export(db_path=db_path)
    cfg = backup["data"]["taskRepeatCfg"]["entities"][PLAIN_TODO_UUID]
    assert cfg["repeatCycle"] == "YEARLY"
    assert cfg["startDate"] == "2020-11-20"


def test_weekly_multi_weekday_recurrence(tmp_path):
    db_path = _copy_fixture_with_recurrence(
        tmp_path,
        PLAIN_TODO_UUID,
        {
            "fu": 256,
            "tp": 0,
            "of": [{"wd": 1}, {"wd": 3}, {"wd": 5}],
            "ia": _epoch_utc_midnight("2024-03-04"),
        },
    )
    backup, _ = build_export(db_path=db_path)
    cfg = backup["data"]["taskRepeatCfg"]["entities"][PLAIN_TODO_UUID]
    assert cfg["repeatCycle"] == "WEEKLY"
    assert [day for day in WEEKDAY_NAMES if cfg[day]] == [
        "monday",
        "wednesday",
        "friday",
    ]


def test_paused_and_repeat_from_completion_flags(tmp_path):
    db_path = _copy_fixture_with_recurrence(
        tmp_path,
        PLAIN_TODO_UUID,
        {"fu": 16, "tp": 1, "of": [{"dy": 0}], "ia": _epoch_utc_midnight("2024-03-05")},
        is_paused=1,
    )
    backup, _ = build_export(db_path=db_path)
    cfg = backup["data"]["taskRepeatCfg"]["entities"][PLAIN_TODO_UUID]
    assert cfg["isPaused"] is True
    assert cfg["repeatFromCompletionDate"] is True


def test_expired_recurrence_treated_as_plain_task(tmp_path):
    """A recurrence whose end date has already passed is no longer
    actually recurring in Things either (it stops generating new
    instances), so it's exported like any other one-off to-do."""
    db_path = _copy_fixture_with_recurrence(
        tmp_path,
        PLAIN_TODO_UUID,
        {
            "fu": 16,
            "tp": 0,
            "of": [{"dy": 0}],
            "ia": _epoch_utc_midnight("2020-01-01"),
            "ed": _epoch_utc_midnight("2020-06-01"),
        },
    )
    backup, stats = build_export(db_path=db_path)
    assert stats["recurring_configs"] == 1  # only the fixture's real one
    assert PLAIN_TODO_UUID not in backup["data"]["taskRepeatCfg"]["entities"]


def _make_rule_blob(**overrides):
    rule = {
        "ed": FAR_FUTURE_END_DATE,
        "fa": 1,
        "fu": 16,
        "ia": _epoch_utc_midnight("2024-01-01"),
        "of": [{"dy": 0}],
        "rc": 0,
        "rrv": 4,
        "sr": _epoch_utc_midnight("2024-01-01"),
        "tp": 0,
        "ts": 0,
        **overrides,
    }
    return plistlib.dumps(rule)


def test_decode_recurrence_rule_rejects_unknown_unit():
    with pytest.raises(ValueError):
        decode_recurrence_rule(_make_rule_blob(fu=999), "some-uuid")


def test_decode_recurrence_rule_rejects_unknown_type():
    with pytest.raises(ValueError):
        decode_recurrence_rule(_make_rule_blob(tp=2), "some-uuid")


def test_decode_recurrence_rule_rejects_limited_occurrence_count():
    with pytest.raises(ValueError):
        decode_recurrence_rule(_make_rule_blob(rc=5), "some-uuid")


def test_decode_recurrence_rule_rejects_multiple_monthly_anchors():
    with pytest.raises(ValueError):
        decode_recurrence_rule(
            _make_rule_blob(fu=8, of=[{"dy": 1}, {"dy": 15}]), "some-uuid"
        )
