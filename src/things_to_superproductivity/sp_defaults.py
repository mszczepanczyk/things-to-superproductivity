"""Default config blobs for Super Productivity's backup schema.

These are static values a fresh Super Productivity instance would have;
verbatim from Super Productivity's own e2e/fixtures/test-backup.json, a
known-good minimal config for the current backup schema (crossModelVersion
4.5).
"""

DEFAULT_THEME = {
    "primary": "#607d8b",
    "accent": "#ff4081",
    "warn": "#e91e63",
    "isAutoContrast": True,
}

DEFAULT_ADVANCED_CFG = {
    "worklogExportSettings": {
        "cols": ["DATE", "START", "END", "TIME_CLOCK", "TITLES_INCLUDING_SUB"],
        "roundWorkTimeTo": None,
        "roundStartTimeTo": None,
        "roundEndTimeTo": None,
        "groupBy": "DATE",
        "separateTasksBy": "",
    }
}

GLOBAL_CONFIG = {
    "misc": {
        "isConfirmBeforeExit": False,
        "isConfirmBeforeExitWithoutFinishDay": True,
        "isNotifyWhenTimeEstimateExceeded": False,
        "isAutMarkParentAsDone": True,
        "isAutoStartNextTask": True,
        "isTurnOffMarkdown": False,
        "isAutoAddWorkedOnToToday": True,
        "isDisableInitialDialog": True,
        "firstDayOfWeek": 1,
        "startOfNextDay": 0,
        "daysToShowForPastDates": 14,
        "isMinimizeToTray": False,
        "isEnableAdvanced": False,
        "isTrayShowCurrentTask": True,
        "taskNotesTpl": "",
        "isDisableAnimations": False,
    },
    "appFeatures": {
        "isTimeTrackingEnabled": True,
        "isFocusModeEnabled": True,
        "isSchedulerEnabled": True,
        "isPlannerEnabled": True,
        "isBoardsEnabled": True,
        "isScheduleDayPanelEnabled": True,
        "isIssuesPanelEnabled": True,
        "isProjectNotesEnabled": True,
        "isSyncIconEnabled": True,
        "isDonatePageEnabled": True,
        "isEnableUserProfiles": False,
    },
    "localization": {},
    "evaluation": {"isHideEvaluationSheet": False},
    "idle": {
        "isEnableIdleTimeTracking": False,
        "isUnTrackedIdleResetsBreakTimer": False,
        "minIdleTime": 60000,
        "isOnlyOpenIdleWhenCurrentTask": False,
    },
    "takeABreak": {
        "isTakeABreakEnabled": False,
        "takeABreakMinWorkingTime": 60,
        "takeABreakSnoozeTime": 15,
        "motivationalImgs": [],
        "isLockScreen": False,
        "isTimedFullScreenBlocker": False,
        "timedFullScreenBlockerDuration": 8000,
        "isFocusWindow": False,
        "takeABreakMessage": "Take a break!",
    },
    "pomodoro": {
        "isEnabled": False,
        "duration": 1500000,
        "breakDuration": 300000,
        "longerBreakDuration": 900000,
        "cyclesBeforeLongerBreak": 4,
        "isStopTrackingOnBreak": True,
        "isStopTrackingOnLongBreak": True,
        "isManualContinue": False,
        "isPlaySound": True,
        "isPlaySoundAfterBreak": False,
        "isEnabled2": False,
    },
    "keyboard": {},
    "localBackup": {"isEnabled": False},
    "lang": {},
    "sync": {
        "isEnabled": False,
        "isCompressionEnabled": True,
        "syncInterval": 0,
        "isEncryptionEnabled": False,
        "syncProvider": None,
    },
    "timeline": {
        "isWorkStartEndEnabled": False,
        "workStart": "9:00",
        "workEnd": "17:00",
    },
    "reminder": {"isCountdownBannerEnabled": True, "countdownDuration": 600000},
    "focusMode": {"isAlwaysUseFocusMode": False, "isSkipPreparation": False},
    "sound": {
        "volume": 70,
        "isPlayDoneSound": True,
        "doneSound": "done2.mp3",
        "isPlayTickSound": False,
        "isIncreaseDoneSoundPitch": True,
        "breakReminderSound": None,
    },
    "timeTracking": {
        "trackingInterval": 1000,
        "defaultEstimate": 0,
        "defaultEstimateSubTasks": 0,
        "isNotifyWhenTimeEstimateExceeded": True,
        "isAutoStartNextTask": False,
        "isTrackingReminderEnabled": False,
        "isTrackingReminderShowOnMobile": False,
        "trackingReminderMinTime": 300000,
    },
    "quickNotes": {},
    "dominaMode": {
        "isEnabled": False,
        "text": "Your current task is: ${currentTaskTitle}",
        "interval": 300000,
        "volume": 75,
    },
    "shortSyntax": {
        "isEnableProject": True,
        "isEnableDue": True,
        "isEnableTag": True,
    },
    "schedule": {
        "isWorkStartEndEnabled": True,
        "workStart": "9:00",
        "workEnd": "17:00",
        "isLunchBreakEnabled": False,
        "lunchBreakStart": "13:00",
        "lunchBreakEnd": "14:00",
    },
}
