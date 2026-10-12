#!/bin/sh
# Restart every watcher, e.g. after updating the plugin. Do not use
# `pkill -f osc7501_watch.py` for this: with `pipe-pane -O` tmux only notices
# that the reader is gone when the pane writes output again, so a quiet pane
# still looks piped and is skipped when the plugin attaches watchers.
#
# Here tmux closes our pipes itself (that is immediate), then every pane is
# attached again. A pane piped by something else (tmux-logging...) is left
# alone: we only close a pipe whose @osc7501_pid is, or was, our watcher.
dir=$(cd "$(dirname "$0")" && pwd)

tmux list-panes -a -F '#{pane_id} #{pane_pipe} #{@osc7501_pid}' 2>/dev/null |
while read -r pane piped pid; do
	[ "$piped" = 1 ] && [ -n "$pid" ] || continue
	case "$(ps -p "$pid" -o args= 2>/dev/null)" in
	*osc7501_watch.py*) ;;                     # our live watcher
	'') ;;                                     # our watcher, already dead
	*) continue ;;                             # the pid now belongs to something else
	esac
	tmux pipe-pane -t "$pane"                  # close it
	tmux set-option -pu -t "$pane" @osc7501_pid
done

tmux list-panes -a -F '#{pane_id}' 2>/dev/null | while read -r pane; do
	"$dir/osc7501_attach.sh" "$pane"
done
