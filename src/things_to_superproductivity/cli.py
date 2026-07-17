"""Command-line entry point for super-productivity-import-things."""

import argparse
import json
import sys

import things.database

from things_to_superproductivity.export import build_export


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Export a Things 3 database into a Super Productivity backup file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-o",
        "--output",
        default="-",
        help="Output path for the Super Productivity backup JSON file. "
        "Use '-' (the default) to write to stdout.",
    )
    parser.add_argument(
        "-d",
        "--things-db",
        default=None,
        help="Path to the Things SQLite database file (main.sqlite). "
        f"Defaults to Things' own database location "
        f"({things.database.DEFAULT_FILEPATH}) or $THINGSDB, if set.",
    )
    parser.add_argument(
        "--no-area-tags",
        action="store_true",
        help="Don't create a tag for tasks that live directly in a Things area "
        "(no project). By default such tasks go to the Inbox project and "
        "get a tag named after their area.",
    )
    parser.add_argument(
        "--no-someday-tags",
        action="store_true",
        help="Don't tag to-dos with a Things 'Someday' start location. "
        "Tagged by default, since Super Productivity has no equivalent "
        "concept.",
    )
    parser.add_argument(
        "--anytime-tags",
        action="store_true",
        help="Tag to-dos with a Things 'Anytime' start location. Not "
        "tagged by default, since Anytime is the common/default state for "
        "most to-dos and would just add noise.",
    )
    parser.add_argument(
        "--keep-dates-on-done",
        action="store_true",
        help="Keep due/deadline dates on already-completed tasks. Dropped by "
        "default: Super Productivity's Today view treats any task with a "
        "past due date as overdue regardless of done state, so completed "
        "Things to-dos with an old 'when' date would otherwise flood it.",
    )
    parser.add_argument(
        "--no-archive-done",
        action="store_true",
        help="Keep completed tasks in the live task/project/tag lists "
        "instead of moving them to Super Productivity's archive (archived "
        "by default). Super Productivity's Today view aggregates every "
        "done task app-wide with no date filter, so importing years of "
        "completed Things to-dos without archiving them floods it.",
    )
    args = parser.parse_args()

    backup, stats = build_export(
        db_path=args.things_db,
        area_tags=not args.no_area_tags,
        someday_tags=not args.no_someday_tags,
        anytime_tags=args.anytime_tags,
        dates_on_done=args.keep_dates_on_done,
        archive_done=not args.no_archive_done,
    )

    if args.output == "-":
        json.dump(backup, sys.stdout, ensure_ascii=False, indent=2)
        print(file=sys.stdout)
        print("Written to stdout", file=sys.stderr)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)
        print(f"Wrote {args.output}", file=sys.stderr)
    print(
        f"  {stats['tasks']} tasks, {stats['subtasks']} sub-tasks, "
        f"{stats['projects']} projects, {stats['tags']} tags, "
        f"{stats['areas']} areas, {stats['recurring_configs']} recurring configs",
        file=sys.stderr,
    )
    if stats["archived_tasks"] or stats["archived_subtasks"]:
        print(
            f"  {stats['archived_tasks']} done tasks, "
            f"{stats['archived_subtasks']} done sub-tasks moved to the archive",
            file=sys.stderr,
        )
    print(
        "\nTo import: in Super Productivity, go to "
        "Settings > Sync & Backup > Import/Export > Import, and pick this file.\n"
        "WARNING: this REPLACES all existing data in Super Productivity - "
        "export a backup of your current Super Productivity data first if you "
        "want to keep it.",
        file=sys.stderr,
    )
