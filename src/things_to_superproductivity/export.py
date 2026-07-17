"""Convert Things 3 data (via things.py) into a Super Productivity backup file.

See README.md for the destructive-import warning and the Things -> Super
Productivity mapping. The output schema itself was reverse engineered from
Super Productivity's own e2e test fixture (e2e/fixtures/test-backup.json)
and source (src/app/op-log/backup/backup.service.ts), matching
crossModelVersion 4.5.
"""

from datetime import datetime

import things

from things_to_superproductivity.sp_defaults import (
    DEFAULT_ADVANCED_CFG,
    DEFAULT_THEME,
    GLOBAL_CONFIG,
)

CROSS_MODEL_VERSION = 4.5

INBOX_PROJECT_ID = "INBOX_PROJECT"


def to_epoch_ms(datetime_str):
    """Convert a Things 'YYYY-MM-DD HH:MM:SS' localtime string to epoch ms."""
    dt = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
    return int(dt.timestamp() * 1000)

def assert_status(status, uuid):
    if status not in ("incomplete", "completed", "canceled"):
        raise ValueError(f"unexpected status {status!r} for {uuid!r}")
    return status


def make_work_context_common(id_, title, task_ids, icon=None):
    return {
        "id": id_,
        "title": title,
        "taskIds": task_ids,
        "icon": icon,
        "theme": dict(DEFAULT_THEME),
        "advancedCfg": {
            "worklogExportSettings": dict(
                DEFAULT_ADVANCED_CFG["worklogExportSettings"]
            )
        },
        "workStart": {},
        "workEnd": {},
        "breakNr": {},
        "breakTime": {},
    }


def make_project(id_, title, task_ids, icon=None, is_archived=False, is_done=False):
    project = make_work_context_common(id_, title, task_ids, icon)
    project.update(
        {
            "isHiddenFromMenu": False,
            "isArchived": is_archived,
            "isDone": is_done,
            "doneOn": None,
            "isEnableBacklog": False,
            "backlogTaskIds": [],
            "noteIds": [],
        }
    )
    return project


def make_tag(id_, title, task_ids, icon=None):
    return make_work_context_common(id_, title, task_ids, icon)


def build_export(
    db_path=None,
    area_tags=True,
    someday_tags=True,
    anytime_tags=False,
    dates_on_done=False,
    archive_done=True,
):
    """Read a Things database and return (backup, stats).

    `backup` is a Super Productivity backup dict, ready to be JSON-dumped
    and imported via Settings > Sync & Backup > Import/Export > Import.
    `stats` is a dict of counts, useful for a CLI summary.

    `someday_tags`/`anytime_tags`: a to-do with a Things "Someday"/"Anytime"
    start location gets a matching tag, since Super Productivity has no
    equivalent concept. "Someday" is tagged by default since it's a
    meaningful, relatively rare distinction; "Anytime" is not, since it's
    the common/default state for most to-dos and would just add noise.

    `dates_on_done`: by default, due/deadline dates are dropped from
    already-done tasks. Set this to True to keep dates on done tasks anyway.

    `archive_done`: by default, already-done tasks are written to Super
    Productivity's archive (archiveYoung) instead of the live task/project/
    tag lists, matching what Super Productivity itself does when a task is
    archived (removed from every project's and tag's taskIds). This matters
    because Super Productivity's Today view aggregates *every* done task in
    the live state app-wide, with no date filter - so importing years of
    completed Things to-dos straight into the live state would otherwise
    flood it. Set this to False to keep done tasks live instead.
    """
    kwargs = {"filepath": db_path} if db_path else {}

    areas = {a["uuid"]: a["title"] for a in things.areas(**kwargs)}
    things_tags = things.tags(**kwargs)
    things_projects = things.projects(status=None, **kwargs)
    headings = things.tasks(type="heading", status=None, **kwargs)
    heading_to_project = {h["uuid"]: h["project"] for h in headings}
    todos = things.todos(status=None, include_items=True, **kwargs)

    # Things' "index" is only meaningful within a single list: a to-do's
    # index under one heading has no relation to another heading's index
    # range, or to the project's own top-level list of headings/direct
    # to-dos. Sorting a project's to-dos by index directly, mixing to-dos
    # from different headings, scrambles the real order. Reconstruct it
    # instead: sort each project's headings and direct to-dos together by
    # their own index, then within each heading sort its to-dos by theirs.
    headings_by_project = {}
    for h in headings:
        headings_by_project.setdefault(h["project"], []).append(h)

    todos_by_heading = {}
    direct_todos_by_project = {}
    inbox_todos = []
    for t in todos:
        if t.get("heading"):
            todos_by_heading.setdefault(t["heading"], []).append(t)
        elif t.get("project"):
            direct_todos_by_project.setdefault(t["project"], []).append(t)
        else:
            inbox_todos.append(t)

    def project_todo_order(project_uuid):
        top_level = sorted(
            headings_by_project.get(project_uuid, [])
            + direct_todos_by_project.get(project_uuid, []),
            key=lambda item: item["index"],
        )
        ordered = []
        for item in top_level:
            if item["type"] == "heading":
                ordered.extend(
                    sorted(
                        todos_by_heading.get(item["uuid"], []),
                        key=lambda t: t["index"],
                    )
                )
            else:
                ordered.append(item)
        return ordered

    ordered_todos = sorted(inbox_todos, key=lambda t: t["index"])
    for p in things_projects:
        ordered_todos.extend(project_todo_order(p["uuid"]))
    if len(ordered_todos) != len(todos):
        raise ValueError("lost or duplicated a to-do while reordering by heading")

    # --- tags ---
    tag_ids = []
    tag_entities = {}
    tag_id_by_title = {}
    for t in things_tags:
        tag_ids.append(t["uuid"])
        tag_entities[t["uuid"]] = make_tag(t["uuid"], t["title"], [])
        tag_id_by_title[t["title"]] = t["uuid"]

    area_tag_id_by_area_uuid = {}
    if area_tags:
        for area_uuid, area_title in areas.items():
            tag_id = f"area-{area_uuid}"
            area_tag_id_by_area_uuid[area_uuid] = tag_id
            tag_ids.append(tag_id)
            tag_entities[tag_id] = make_tag(tag_id, area_title, [])

    special_tag_id_by_name = {}

    def get_special_tag_id(name):
        if name not in special_tag_id_by_name:
            tag_id = f"tag-{name.lower()}"
            special_tag_id_by_name[name] = tag_id
            tag_ids.append(tag_id)
            tag_entities[tag_id] = make_tag(tag_id, name, [])
        return special_tag_id_by_name[name]

    # --- projects (+ notes) ---
    project_ids = [INBOX_PROJECT_ID]
    project_entities = {
        INBOX_PROJECT_ID: make_project(INBOX_PROJECT_ID, "Inbox", [], icon="inbox")
    }
    note_ids = []
    note_entities = {}
    for p in things_projects:
        p_status = assert_status(p["status"], p["uuid"])
        project_ids.append(p["uuid"])
        project_entities[p["uuid"]] = make_project(
            p["uuid"],
            p["title"],
            [],
            is_archived=(p_status != "incomplete"),
            is_done=(p_status == "completed"),
        )

        if p["notes"]:
            note_id = f"note-{p['uuid']}"
            note_ids.append(note_id)
            note_entities[note_id] = {
                "id": note_id,
                "projectId": p["uuid"],
                "isPinnedToToday": False,
                "content": p["notes"],
                "created": to_epoch_ms(p["created"]),
                "modified": to_epoch_ms(p["modified"]),
            }
            project_entities[p["uuid"]]["noteIds"].append(note_id)

    # --- tasks (+ checklist items as sub-tasks) ---
    task_ids = []
    task_entities = {}
    archive_task_ids = []
    archive_task_entities = {}

    for todo in ordered_todos:
        status = assert_status(todo["status"], todo["uuid"])

        is_done = status in ("completed", "canceled")
        archive_this_task = archive_done and is_done
        if is_done:
            if not todo["stop_date"]:
                raise ValueError(f"done to-do {todo['uuid']!r} has no stop_date")
            done_on = to_epoch_ms(todo["stop_date"])
        else:
            done_on = None

        project_id = todo.get("project")
        heading_title = None
        if not project_id and todo.get("heading"):
            project_id = heading_to_project[todo["heading"]]
            heading_title = todo["heading_title"]
        if not project_id:
            project_id = INBOX_PROJECT_ID

        this_tag_ids = [
            tag_id_by_title[title] for title in (todo.get("tags") or [])
        ]
        area_uuid = todo.get("area")
        if area_tags and area_uuid:
            this_tag_ids.append(area_tag_id_by_area_uuid[area_uuid])
        if someday_tags and todo["start"] == "Someday":
            this_tag_ids.append(get_special_tag_id("Someday"))
        if anytime_tags and todo["start"] == "Anytime":
            this_tag_ids.append(get_special_tag_id("Anytime"))
        if status == "canceled":
            this_tag_ids.append(get_special_tag_id("Canceled"))

        title = f"{heading_title} > {todo['title']}" if heading_title else todo["title"]

        sub_task_ids = []
        for item in todo.get("checklist") or []:
            sub_id = item["uuid"]
            sub_task_ids.append(sub_id)
            sub_entity = {
                "id": sub_id,
                "title": item["title"],
                "projectId": project_id,
                "parentId": todo["uuid"],
                "subTaskIds": [],
                "timeSpentOnDay": {},
                "timeSpent": 0,
                "timeEstimate": 0,
                "isDone": assert_status(item["status"], item["uuid"]) != "incomplete",
                "notes": "",
                "tagIds": [],
                "created": to_epoch_ms(item["created"]),
                "attachments": [],
            }
            if archive_this_task:
                # matches Super Productivity's own archiving fallback: a
                # subtask's doneOn defaults to its parent's when archived
                if sub_entity["isDone"]:
                    sub_entity["doneOn"] = done_on
                archive_task_ids.append(sub_id)
                archive_task_entities[sub_id] = sub_entity
            else:
                task_ids.append(sub_id)
                task_entities[sub_id] = sub_entity

        task = {
            "id": todo["uuid"],
            "title": title,
            "projectId": project_id,
            "subTaskIds": sub_task_ids,
            "timeSpentOnDay": {},
            "timeSpent": 0,
            "timeEstimate": 0,
            "isDone": is_done,
            "notes": todo["notes"],
            "tagIds": this_tag_ids,
            "created": to_epoch_ms(todo["created"]),
            "attachments": [],
        }
        if dates_on_done or not is_done:
            if todo.get("start_date"):
                task["dueDay"] = todo["start_date"]
            if todo.get("deadline"):
                task["deadlineDay"] = todo["deadline"]
        if is_done:
            task["doneOn"] = done_on

        if archive_this_task:
            archive_task_ids.append(todo["uuid"])
            archive_task_entities[todo["uuid"]] = task
        else:
            task_ids.append(todo["uuid"])
            task_entities[todo["uuid"]] = task
            project_entities[project_id]["taskIds"].append(todo["uuid"])
            for tid in this_tag_ids:
                tag_entities[tid]["taskIds"].append(todo["uuid"])

    data = {
        "project": {"ids": project_ids, "entities": project_entities},
        "menuTree": {"tagTree": [], "projectTree": []},
        "globalConfig": GLOBAL_CONFIG,
        "task": {
            "ids": task_ids,
            "entities": task_entities,
            "currentTaskId": None,
            "selectedTaskId": None,
            "lastCurrentTaskId": None,
            "isDataLoaded": False,
        },
        "tag": {"ids": tag_ids, "entities": tag_entities},
        "simpleCounter": {"ids": [], "entities": {}},
        "taskRepeatCfg": {"ids": [], "entities": {}},
        "reminders": [],
        "note": {"ids": note_ids, "entities": note_entities, "todayOrder": []},
        "metric": {"ids": [], "entities": {}},
        "planner": {"days": {}},
        "issueProvider": {"ids": [], "entities": {}},
        "boards": {"boardCfgs": []},
        "timeTracking": {"project": {}, "tag": {}},
        "archiveYoung": {
            "task": {"ids": archive_task_ids, "entities": archive_task_entities},
            "timeTracking": {"project": {}, "tag": {}},
        },
        "archiveOld": {
            "task": {"ids": [], "entities": {}},
            "timeTracking": {"project": {}, "tag": {}},
        },
        "pluginMetadata": [],
        "pluginUserData": [],
    }

    now = int(datetime.now().timestamp() * 1000)
    backup = {
        "timestamp": now,
        "lastUpdate": now,
        "crossModelVersion": CROSS_MODEL_VERSION,
        "data": data,
    }

    stats = {
        "projects": len(things_projects),
        "tags": len(things_tags),
        "areas": len(areas),
        "tasks": len(task_ids)
        - sum(len(e.get("subTaskIds", [])) for e in task_entities.values()),
        "subtasks": sum(len(e.get("subTaskIds", [])) for e in task_entities.values()),
        "archived_tasks": len(archive_task_ids)
        - sum(len(e.get("subTaskIds", [])) for e in archive_task_entities.values()),
        "archived_subtasks": sum(
            len(e.get("subTaskIds", [])) for e in archive_task_entities.values()
        ),
    }
    return backup, stats
