"""Convert Things 3 data (via things.py) into a Super Productivity backup file.

See README.md for the destructive-import warning and the Things -> Super
Productivity mapping. The output schema itself was reverse engineered from
Super Productivity's own e2e test fixture (e2e/fixtures/test-backup.json)
and source (src/app/op-log/backup/backup.service.ts), matching
crossModelVersion 4.5. The menuTree folder shape (grouping projects/tags for
display) comes from src/app/features/menu-tree/store/menu-tree.model.ts and
is validated by src/app/op-log/validation/is-related-model-data-valid.ts.
"""

import plistlib
from datetime import datetime, timezone

import things
from things.database import make_tasks_sql_query

from things_to_superproductivity.sp_defaults import (
    DEFAULT_ADVANCED_CFG,
    DEFAULT_THEME,
    GLOBAL_CONFIG,
)

CROSS_MODEL_VERSION = 4.5

INBOX_PROJECT_ID = "INBOX_PROJECT"

# Super Productivity's menuTree node kinds (src/app/features/menu-tree/store/
# menu-tree.model.ts MenuTreeKind): 'f' groups children under a named,
# purely-visual folder; 'p'/'t' are references to a real project/tag entity.
# Any project/tag id not mentioned anywhere in the tree is appended at the
# top level automatically (menu-tree.service.ts _buildViewTree), so this
# only needs to list the ids worth grouping - everything else is left out
# and falls back to that default flat placement.
MENU_TREE_FOLDER = "f"
MENU_TREE_PROJECT = "p"
MENU_TREE_TAG = "t"

# Things' rt1_recurrenceRule is an undocumented binary plist. This mapping
# was reverse engineered against a real Things database (24 recurring
# to-dos spanning all four cycles), cross-checked against each to-do's
# actual configured schedule - there's no public documentation of this
# format to verify against otherwise.
RECURRENCE_UNIT_TO_CYCLE = {4: "YEARLY", 8: "MONTHLY", 16: "DAILY", 256: "WEEKLY"}
WEEKDAY_NAMES = [
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
]


def to_epoch_ms(datetime_str):
    """Convert a Things 'YYYY-MM-DD HH:MM:SS' localtime string to epoch ms."""
    dt = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
    return int(dt.timestamp() * 1000)


def to_epoch_ms_with_time(date_str, time_str):
    """Combine a Things 'YYYY-MM-DD' start date and 'HH:MM' reminder time
    (both localtime) into epoch ms."""
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return int(dt.timestamp() * 1000)


def assert_status(status, uuid):
    if status not in ("incomplete", "completed", "canceled"):
        raise ValueError(f"unexpected status {status!r} for {uuid!r}")
    return status


def thingsdate_to_isodate(epoch_seconds):
    """Convert a rt1_recurrenceRule plist date (seconds since the Unix
    epoch, at UTC midnight) to an ISO date string."""
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).date().isoformat()


def decode_recurrence_rule(blob, uuid):
    """Decode a Things rt1_recurrenceRule plist blob into the pieces needed
    for a Super Productivity taskRepeatCfg.

    Raises rather than guesses for any shape not seen in the real database
    this was reverse engineered against (a limited occurrence count, or
    more than one monthly/yearly anchor) - a silently wrong recurrence
    schedule is worse than a to-do exported without one.
    """
    rule = plistlib.loads(blob)

    unit = rule.get("fu")
    if unit not in RECURRENCE_UNIT_TO_CYCLE:
        raise ValueError(f"unknown Things recurrence unit {unit!r} for {uuid!r}")
    cycle = RECURRENCE_UNIT_TO_CYCLE[unit]

    recurrence_type = rule.get("tp")
    if recurrence_type not in (0, 1):
        raise ValueError(
            f"unknown Things recurrence type {recurrence_type!r} for {uuid!r}"
        )

    if rule.get("rc"):
        raise ValueError(
            f"recurring to-do {uuid!r} has a limited occurrence count, which "
            "Super Productivity's taskRepeatCfg has no equivalent for"
        )

    offsets = rule.get("of") or []
    weekday_flags = {}
    monthly_last_day = False
    if cycle == "WEEKLY":
        for offset in offsets:
            weekday_flags[WEEKDAY_NAMES[offset["wd"]]] = True
    elif cycle in ("MONTHLY", "YEARLY"):
        if len(offsets) > 1:
            raise ValueError(
                f"recurring to-do {uuid!r} has more than one {cycle.lower()} "
                "anchor, which Super Productivity's taskRepeatCfg has no "
                "equivalent for"
            )
        if cycle == "MONTHLY" and offsets and offsets[0].get("dy") == -1:
            monthly_last_day = True

    end_date = thingsdate_to_isodate(rule["ed"]) if rule.get("ed") else None
    today = datetime.now(timezone.utc).date().isoformat()

    return {
        "cycle": cycle,
        "repeat_every": rule.get("fa") or 1,
        "start_date": thingsdate_to_isodate(rule["ia"]),
        "weekday_flags": weekday_flags,
        "monthly_last_day": monthly_last_day,
        "repeat_from_completion": recurrence_type == 1,
        "is_expired": end_date is not None and end_date <= today,
    }


def make_task_repeat_cfg(
    id_, title, project_id, tag_ids, notes, is_paused, rule, start_time=None
):
    now = datetime.now(timezone.utc)
    cfg = {
        "id": id_,
        "projectId": project_id,
        "title": title,
        "tagIds": tag_ids,
        "order": 0,
        "defaultEstimate": None,
        "startTime": start_time,
        "remindAt": None,
        "isPaused": is_paused,
        "quickSetting": "CUSTOM",
        "repeatCycle": rule["cycle"],
        "startDate": rule["start_date"],
        "repeatEvery": rule["repeat_every"],
        "notes": notes or None,
        "shouldInheritSubtasks": False,
        "repeatFromCompletionDate": rule["repeat_from_completion"],
        "waitForCompletion": False,
        "disableAutoUpdateSubtasks": False,
        "skipOverdue": False,
        # Anchored to "now" rather than any real last-generated-instance
        # date: Things' own past instances are exported as plain to-dos
        # (see repeatCfgId assignment in build_export), so Super
        # Productivity has no backlog to catch up on - only future
        # occurrences should come from this cfg.
        "lastTaskCreation": int(now.timestamp() * 1000),
        "lastTaskCreationDay": now.date().isoformat(),
    }
    for day in WEEKDAY_NAMES:
        cfg[day] = rule["weekday_flags"].get(day, False)
    if rule["monthly_last_day"]:
        cfg["monthlyLastDay"] = True
    return cfg


def resolve_project_id_and_title(todo, heading_to_project):
    project_id = todo.get("project")
    heading_title = None
    if not project_id and todo.get("heading"):
        project_id = heading_to_project[todo["heading"]]
        heading_title = todo["heading_title"]
    if not project_id:
        project_id = INBOX_PROJECT_ID
    title = f"{heading_title} > {todo['title']}" if heading_title else todo["title"]
    return project_id, title


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

    # --- project menu folders (Things area -> a menuTree folder per area) ---
    # Super Productivity's project model has no area/parent field, so a
    # project's area membership would otherwise vanish entirely on import.
    # Folders are purely a display grouping (see MENU_TREE_FOLDER above),
    # so this only needs an entry per project that actually has an area;
    # everything else keeps landing at the top level like before.
    project_tree = []
    if area_tags:
        project_ids_by_area_uuid = {}
        for p in things_projects:
            area_uuid = p.get("area")
            if area_uuid:
                project_ids_by_area_uuid.setdefault(area_uuid, []).append(p["uuid"])
        for area_uuid, area_title in areas.items():
            area_project_ids = project_ids_by_area_uuid.get(area_uuid)
            if area_project_ids:
                project_tree.append(
                    {
                        "id": f"area-folder-{area_uuid}",
                        "k": MENU_TREE_FOLDER,
                        "name": area_title,
                        "children": [
                            {"id": pid, "k": MENU_TREE_PROJECT}
                            for pid in area_project_ids
                        ],
                    }
                )

    # --- recurring task templates ---
    # things.py excludes every recurring template from all its query
    # methods (their SQL always filters `rt1_recurrenceRule IS NULL`), so
    # templates have to be queried directly off the underlying table.
    db = things.Database(**kwargs)

    # --- tag menu folders (Things nested tags -> a menuTree folder per
    # parent tag) --- things.py's own get_tags() doesn't select TMTag.parent
    # at all, so it's queried directly here, the same way recurring
    # templates are below. A parent tag stays a real, independently
    # usable top-level tag (Super Productivity tags have no hierarchy of
    # their own); only its children move into a folder named after it.
    tag_tree = []
    folder_by_parent_tag_uuid = {}
    for row in db.execute_query("SELECT uuid, parent FROM TMTag WHERE parent IS NOT NULL"):
        child_uuid, parent_uuid = row["uuid"], row["parent"]
        if child_uuid not in tag_entities or parent_uuid not in tag_entities:
            continue  # orphaned reference (e.g. parent tag was trashed)
        folder = folder_by_parent_tag_uuid.get(parent_uuid)
        if folder is None:
            folder = {
                "id": f"tag-folder-{parent_uuid}",
                "k": MENU_TREE_FOLDER,
                "name": tag_entities[parent_uuid]["title"],
                "children": [],
            }
            folder_by_parent_tag_uuid[parent_uuid] = folder
            tag_tree.append(folder)
        folder["children"].append({"id": child_uuid, "k": MENU_TREE_TAG})

    template_rows = db.execute_query(
        make_tasks_sql_query(
            where_predicate="TASK.rt1_recurrenceRule IS NOT NULL AND TASK.trashed = 0"
        )
    )
    recurrence_raw_by_uuid = {
        row["uuid"]: row
        for row in db.execute_query(
            "SELECT uuid, rt1_recurrenceRule AS recurrence_rule, "
            "rt1_instanceCreationPaused AS is_paused FROM TMTask "
            "WHERE rt1_recurrenceRule IS NOT NULL AND trashed = 0"
        )
    }
    instance_to_template = {
        row["uuid"]: row["template_uuid"]
        for row in db.execute_query(
            "SELECT uuid, rt1_repeatingTemplate AS template_uuid FROM TMTask "
            "WHERE rt1_repeatingTemplate IS NOT NULL AND rt1_repeatingTemplate != ''"
        )
    }

    repeat_cfg_ids = []
    repeat_cfg_entities = {}
    for row in template_rows:
        raw = recurrence_raw_by_uuid[row["uuid"]]
        rule = decode_recurrence_rule(raw["recurrence_rule"], row["uuid"])
        if rule["is_expired"]:
            # Things itself stopped generating instances for this
            # to-do - treat it like any other non-recurring to-do.
            continue
        template_project_id, template_title = resolve_project_id_and_title(
            row, heading_to_project
        )
        template_tag_ids = [
            tag_id_by_title[t] for t in (db.get_tags(task=row["uuid"]) or [])
        ]
        repeat_cfg_ids.append(row["uuid"])
        repeat_cfg_entities[row["uuid"]] = make_task_repeat_cfg(
            row["uuid"],
            template_title,
            template_project_id,
            template_tag_ids,
            row["notes"],
            bool(raw["is_paused"]),
            rule,
            row.get("reminder_time"),
        )

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

        project_id, title = resolve_project_id_and_title(todo, heading_to_project)

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
                # dueWithTime and dueDay are mutually exclusive in Super
                # Productivity's task model - a to-do with a Things reminder
                # time gets the precise timestamp instead of a bare day, so
                # it shows up in SP's "Scheduled" list.
                if todo.get("reminder_time"):
                    task["dueWithTime"] = to_epoch_ms_with_time(
                        todo["start_date"], todo["reminder_time"]
                    )
                else:
                    task["dueDay"] = todo["start_date"]
            if todo.get("deadline"):
                task["deadlineDay"] = todo["deadline"]
        if is_done:
            task["doneOn"] = done_on
        repeat_cfg_id = instance_to_template.get(todo["uuid"])
        if repeat_cfg_id in repeat_cfg_entities:
            task["repeatCfgId"] = repeat_cfg_id

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
        "menuTree": {"tagTree": tag_tree, "projectTree": project_tree},
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
        "taskRepeatCfg": {"ids": repeat_cfg_ids, "entities": repeat_cfg_entities},
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
        "recurring_configs": len(repeat_cfg_ids),
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
