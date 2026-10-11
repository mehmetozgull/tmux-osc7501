#!/usr/bin/env bash
# tmux-osc7501 – show OSC 7501 (Program Status Protocol) reports in tmux.
#
# Load from ~/.tmux.conf, either with TPM:
#     set -g @plugin '<you>/tmux-osc7501'
# or directly:
#     run-shell ~/.tmux/plugins/tmux-osc7501/osc7501.tmux

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ATTACH="$DIR/scripts/osc7501_attach.sh"
SEEN="$DIR/scripts/osc7501_seen.sh"

opt() {
	local v
	v="$(tmux show-option -gqv "$1")"
	if [ -n "$v" ]; then printf '%s' "$v"; else printf '%s' "$2"; fi
}

fail() {
	tmux display-message "tmux-osc7501: $1"
	exit 0
}

# --- requirements ---------------------------------------------------------
ver="$(tmux -V | sed -E 's/^[^0-9]*([0-9]+)\.([0-9]+).*/\1 \2/')"
major="${ver% *}"
minor="${ver#* }"
case "$major$minor" in *[!0-9]*|'') major=99 minor=0 ;; esac # "tmux master" etc.
if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 2 ]; }; then
	fail "tmux 3.2 or newer is required (found: $(tmux -V))"
fi
py="$(opt @osc7501-python python3)"
command -v "$py" >/dev/null 2>&1 || fail "'$py' not found (set @osc7501-python)"
chmod +x "$ATTACH" "$SEEN" "$DIR/bin/osc7501" "$DIR/scripts/osc7501_watch.py" 2>/dev/null

# --- watch every pane -------------------------------------------------------
# Hook index 7501 keeps our hooks separate from the user's and makes reloading
# the config idempotent.
tmux set-hook -g 'after-new-window[7501]' "run-shell -b \"'$ATTACH' '#{pane_id}'\""
tmux set-hook -g 'after-split-window[7501]' "run-shell -b \"'$ATTACH' '#{pane_id}'\""
tmux set-hook -g 'session-created[7501]' "run-shell -b \"'$ATTACH' '#{pane_id}'\""

tmux list-panes -a -F '#{pane_id}' | while read -r pane; do
	"$ATTACH" "$pane"
done

# --- "seen": switching to a window clears its done/error markers -------------
tmux set-hook -g 'session-window-changed[7501]' "run-shell -b \"'$SEEN' '#{window_id}'\""
tmux set-hook -g 'client-session-changed[7501]' "run-shell -b \"'$SEEN' '#{window_id}'\""

# --- status line ------------------------------------------------------------
# Put #{osc7501} in window-status-format / window-status-current-format to
# choose where the marker goes. Otherwise it is appended (set
# @osc7501-status-append off to leave the formats alone).
LABEL='#{?@osc7501_label, #{@osc7501_label},}'
append="$(opt @osc7501-status-append on)"
for name in window-status-format window-status-current-format; do
	cur="$(tmux show-option -gv "$name")"
	case "$cur" in
	*'#{osc7501}'*)
		tmux set-option -g "$name" "${cur//'#{osc7501}'/$LABEL}"
		;;
	*'@osc7501_label'*) ;; # already there (config reloaded)
	*)
		[ "$append" = on ] && tmux set-option -g "$name" "$cur$LABEL"
		;;
	esac
done

# --- inbox: list the windows that report something --------------------------
key="$(opt @osc7501-inbox-key A)"
if [ "$key" != off ]; then
	tmux bind-key "$key" choose-tree -Zw \
		-f '#{&&:#{@osc7501_window_state},#{!=:#{@osc7501_window_state},idle}}' \
		-F '#{?@osc7501_window_state,[#{@osc7501_window_state}] #{@osc7501_window_app} #{@osc7501_window_msg},}'
fi
