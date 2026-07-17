# things-to-superproductivity

Export a [Things 3](https://culturedcode.com/things/) database into a
[Super Productivity](https://super-productivity.com/) backup file, using
[things.py](https://thingsapi.github.io/things.py/things/api.html) to read
the Things SQLite database.

Super Productivity has no incremental/additive JSON import - the only
supported way to bring in outside data is via **Settings > Sync & Backup >
Import/Export > Import**, which does a full-state restore. **Importing the
file this tool produces will replace all existing data in Super
Productivity** - export a backup of your current Super Productivity data
first if you have anything in there you want to keep.

Vibe coded with care, YMMV.

## Usage

```sh
> uvx --from git+https://github.com/mszczepanczyk/things-to-superproductivity things-to-superproductivity --help
      Built things-to-superproductivity @ git+https://github.com/mszczepanczyk/things-to-superproductivity@267cdc75c26f01b26ef2b9a407746711
Installed 2 packages in 3ms
usage: things-to-superproductivity [-h] [-o OUTPUT] [-d THINGS_DB] [--no-area-tags] [--no-someday-tags] [--anytime-tags]
                                   [--keep-dates-on-done] [--no-archive-done]

Export a Things 3 database into a Super Productivity backup file.

options:
  -h, --help            show this help message and exit
  -o, --output OUTPUT   Output path for the Super Productivity backup JSON file. Use '-' (the default) to write to stdout.
  -d, --things-db THINGS_DB
                        Path to the Things SQLite database file (main.sqlite). Defaults to Things' own database location
                        (/home/mariusz/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/Things
                        Database.thingsdatabase/main.sqlite) or $THINGSDB, if set.
  --no-area-tags        Don't create a tag for tasks that live directly in a Things area (no project). By default such tasks go to the
                        Inbox project and get a tag named after their area.
  --no-someday-tags     Don't tag to-dos with a Things 'Someday' start location. Tagged by default, since Super Productivity has no
                        equivalent concept.
  --anytime-tags        Tag to-dos with a Things 'Anytime' start location. Not tagged by default, since Anytime is the common/default
                        state for most to-dos and would just add noise.
  --keep-dates-on-done  Keep due/deadline dates on already-completed tasks. Dropped by default: Super Productivity's Today view treats
                        any task with a past due date as overdue regardless of done state, so completed Things to-dos with an old 'when'
                        date would otherwise flood it.
  --no-archive-done     Keep completed tasks in the live task/project/tag lists instead of moving them to Super Productivity's archive
                        (archived by default). Super Productivity's Today view aggregates every done task app-wide with no date filter,
                        so importing years of completed Things to-dos without archiving them floods it.
```

## Mapping

| Things | Super Productivity |
|---|---|
| Project | Project (notes -> a linked Note entity) |
| Tag | Tag |
| Checklist item | Sub-task |
| Deadline | `deadlineDay` |
| Start/when date | `dueDay` (or `dueWithTime` if the to-do also has a Things reminder time) |
| Task notes | Task notes |
| Area (task with no project) | filed in Inbox, tagged with the area name |
| Heading | not a container in Super Productivity; task title becomes "Heading > Title" |
| Someday start location | a "Someday" tag (see `--no-someday-tags`) |
| Anytime start location | no tag by default (see `--anytime-tags`) |
| Inbox start location | no tag - these to-dos have no project or area either, so they land in the Inbox project |
| Canceled to-do | marked done, tagged "Canceled" (always on) |
| Completed/canceled to-do | moved to the archive (see `--no-archive-done`) |
| Recurring to-do | a `taskRepeatCfg`, linked back to every instance to-do via `repeatCfgId` (checklist items are not carried onto *future* instances Super Productivity generates - only onto the ones already in Things); a reminder time on the template becomes the cfg's `startTime` |

## Development

```sh
uv run pytest
```

Tests use the sample Things database from
[things.py's own test suite](https://github.com/thingsapi/things.py/tree/main/tests),
copied into `tests/fixtures/` since it isn't shipped in the installed
`things.py` package.
