"""Change detection — diffs the target codebase against the baseline tag.

Exports:
    GitWatcher      — subprocess wrapper around git diff/log
    ChangeAnalyzer  — Claude-powered diff impact analysis (cached by HEAD SHA)
"""
from change_detection.git_watcher import GitWatcher
from change_detection.change_analyzer import ChangeAnalyzer

__all__ = ["GitWatcher", "ChangeAnalyzer"]
