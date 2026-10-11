#!/bin/sh
# Called when something comes on screen: tell the watchers that their
# `done` / `error` results have been seen, so the markers disappear.
#
#   osc7501_seen.sh window WINDOW_ID   a window was switched to
#   osc7501_seen.sh pane PANE_ID       a pane was selected or got the focus
#
# Pane scope (default): only the pane you look at counts – the selected pane,
# or the active pane of the window you switched to. Window scope: every pane
# of the window you switched to counts, and selecting a pane does nothing.
what="$1"
target="$2"
[ -n "$target" ] || exit 0
[ "$(tmux show-option -gqv @osc7501-clear-on-visit)" = off ] && exit 0
scope=$(tmux show-option -gqv @osc7501-scope)
[ "$scope" = window ] || scope=pane # like the status line: anything else is pane

case "$what/$scope" in
window/window)
	pids=$(tmux list-panes -t "$target" -F '#{@osc7501_pid}' 2>/dev/null) ;;
window/pane)
	pids=$(tmux display-message -p -t "$target" '#{@osc7501_pid}' 2>/dev/null) ;;
pane/pane)
	# A script selecting a pane in a window you can't see does not count.
	pids=$(tmux display-message -p -t "$target" '#{?window_active,#{@osc7501_pid},}' 2>/dev/null) ;;
*)
	exit 0 ;;
esac

for pid in $pids; do
	# Only signal a live watcher – never a process that reused the pid.
	case "$(ps -p "$pid" -o args= 2>/dev/null)" in
	*osc7501_watch.py*) kill -USR1 "$pid" 2>/dev/null ;;
	esac
done
exit 0
