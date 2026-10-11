#!/bin/sh
# Start the OSC 7501 watcher on one pane, unless the pane is already piped
# (by us, or by something else such as tmux-logging – we never take over).
pane="$1"
[ -n "$pane" ] || exit 0
dir=$(cd "$(dirname "$0")" && pwd)
piped=$(tmux display-message -p -t "$pane" '#{pane_pipe}' 2>/dev/null) || exit 0
[ "$piped" = 1 ] && exit 0
py=$(tmux show-option -gqv @osc7501-python)
[ -n "$py" ] || py=python3
exec tmux pipe-pane -O -t "$pane" "exec '$py' '$dir/osc7501_watch.py' '#{pane_id}' '#{socket_path}'"
