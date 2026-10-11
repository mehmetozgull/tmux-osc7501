#!/bin/sh
# Called when a window comes on screen: tell each pane's watcher that its
# `done` / `error` results have been seen, so the marker disappears.
win="$1"
[ -n "$win" ] || exit 0
[ "$(tmux show-option -gqv @osc7501-clear-on-visit)" = off ] && exit 0
tmux list-panes -t "$win" -F '#{@osc7501_pid}' 2>/dev/null | while read -r pid; do
	[ -n "$pid" ] || continue
	# Only signal a live watcher – never a process that reused the pid.
	case "$(ps -p "$pid" -o args= 2>/dev/null)" in
	*osc7501_watch.py*) kill -USR1 "$pid" 2>/dev/null ;;
	esac
done
exit 0
