#!/usr/bin/env bash
# tmux-osc7501 – show OSC 7501 (Program Status Protocol) reports in tmux.
#
# Load from ~/.tmux.conf, either with TPM:
#     set -g @plugin 'mehmetozgull/tmux-osc7501'
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

# --- "seen": what comes on screen loses its done/error markers ---------------
# Pane scope (default): the pane you select (keys or mouse) or the active pane
# of the window you switch to. pane-focus-in also covers last-pane (prefix ;)
# but only fires with focus-events on. Window scope: the whole window.
tmux set-hook -g 'session-window-changed[7501]' "run-shell -b \"'$SEEN' window '#{window_id}'\""
tmux set-hook -g 'client-session-changed[7501]' "run-shell -b \"'$SEEN' window '#{window_id}'\""
tmux set-hook -g 'after-select-pane[7501]' "run-shell -b \"'$SEEN' pane '#{pane_id}'\""
tmux set-hook -g 'pane-focus-in[7501]' "run-shell -b \"'$SEEN' pane '#{pane_id}'\""

# --- status line and pane borders --------------------------------------------
# The formats only reference #{E:@osc7501_status} / #{E:@osc7501_pane_status},
# so a reload can change what they show without touching the formats again.
scope="$(opt @osc7501-scope pane)"
if [ "$scope" = window ]; then
	# The most important pane of the window, with its app.
	tmux set-option -g @osc7501_status '#{?@osc7501_label, #{@osc7501_label},}'
else
	# One pane: its label. Several panes: "index+icon" for each pane that reports.
	tmux set-option -g @osc7501_status '#{?#{==:#{window_panes},1},#{P:#{?@osc7501_pane_label, #{@osc7501_pane_label},}},#{P:#{?@osc7501_pane_icon, #{pane_index}#{@osc7501_pane_icon},}}}'
fi
tmux set-option -g @osc7501_pane_status '#{?@osc7501_pane_label, #{@osc7501_pane_label},}'

PLACEHOLDER='#{osc7501}'
OLD_LABEL='#{?@osc7501_label, #{@osc7501_label},}' # what older versions appended

# place OPTION REF APPEND: put REF where the user wrote #{osc7501}, upgrade
# the old label, or append REF when APPEND is on.
place() {
	local cur new
	cur="$(tmux show-option -gv "$1")"
	case "$cur" in
	*"$2"*) return ;; # already there (config reloaded)
	*"$PLACEHOLDER"*) new=${cur//"$PLACEHOLDER"/$2} ;;
	*"$OLD_LABEL"*) new=${cur//"$OLD_LABEL"/$2} ;;
	*) [ "$3" = on ] || return; new="$cur$2" ;;
	esac
	tmux set-option -g "$1" "$new"
}

append="$(opt @osc7501-status-append on)"
place window-status-format '#{E:@osc7501_status}' "$append"
place window-status-current-format '#{E:@osc7501_status}' "$append"

border="$(opt @osc7501-pane-border off)"
if [ "$border" = on ] && [ "$(tmux show-option -gv pane-border-status)" = off ]; then
	tmux set-option -g pane-border-status top
fi
place pane-border-format '#{E:@osc7501_pane_status}' "$border"

# --- line colours ---------------------------------------------------------------
# @osc7501_line_own: the pane's own state colour (for a top line drawn in
# pane-border-format). With @osc7501-left-lines on, each pane also owns its
# left border: tmux draws the border between two panes as part of the left
# one, so its style reads @osc7501_rn_state, the right neighbour's state,
# which the watchers and the window-layout-changed hook keep up to date.
lc_active="$(opt @osc7501-line-active brightcyan)"
lc_idle="$(opt @osc7501-line-idle colour240)"
lc_blocked="$(opt @osc7501-line-blocked brightred)"
lc_error="$(opt @osc7501-line-error brightred)"
lc_working="$(opt @osc7501-line-working brightyellow)"
lc_done="$(opt @osc7501-line-done brightgreen)"
colour() { # colour STATE_FORMAT FALLBACK_STYLE
	printf '#{?#{==:%s,blocked},fg=%s,#{?#{==:%s,error},fg=%s,#{?#{==:%s,working},fg=%s,#{?#{==:%s,done},fg=%s,%s}}}}' \
		"$1" "$lc_blocked" "$1" "$lc_error" "$1" "$lc_working" "$1" "$lc_done" "$2"
}
# Build the values in plain variables: bash 3.2 brace-expands a
# "#{?a,b,c}" nested inside "$(...)" after a line continuation.
own_fallback="#{?pane_active,fg=$lc_active,fg=$lc_idle}"
own_colour=$(colour '#{@osc7501_state}' "$own_fallback")
tmux set-option -g @osc7501_line_own "$own_colour"
if [ "$(opt @osc7501-left-lines off)" = on ]; then
	border_colour=$(colour '#{@osc7501_rn_state}' "fg=$lc_idle")
	# a border of the active pane: if the left pane is the active one this is
	# the right pane's left line (not active), otherwise the right pane is active
	active_fallback="#{?pane_active,fg=$lc_idle,fg=$lc_active}"
	active_colour=$(colour '#{@osc7501_rn_state}' "$active_fallback")
	tmux set-option -g pane-border-style "$border_colour"
	tmux set-option -g pane-active-border-style "$active_colour"
	tmux set-hook -g 'window-layout-changed[7501]' \
		"run-shell -b \"'$py' '$DIR/scripts/osc7501_watch.py' --neighbours '#{window_id}'\""
	tmux list-windows -a -F '#{window_id}' | while read -r win; do
		"$py" "$DIR/scripts/osc7501_watch.py" --neighbours "$win"
	done
else
	tmux set-hook -gu 'window-layout-changed[7501]'
fi

# --- inbox: list what reports something ----------------------------------------
key="$(opt @osc7501-inbox-key A)"
if [ "$key" != off ] && [ "$scope" = window ]; then
	tmux bind-key "$key" choose-tree -Zw \
		-f '#{&&:#{@osc7501_window_state},#{!=:#{@osc7501_window_state},idle}}' \
		-F '#{?@osc7501_window_state,[#{@osc7501_window_state}] #{@osc7501_window_app} #{@osc7501_window_msg},}'
elif [ "$key" != off ]; then
	tmux bind-key "$key" choose-tree -Z \
		-f '#{&&:#{@osc7501_state},#{!=:#{@osc7501_state},idle}}' \
		-F '#{?pane_format,[#{@osc7501_state}] #{@osc7501_app} #{pane_title}#{?@osc7501_msg, – #{@osc7501_msg},},#{?window_format,#{window_name},}}'
fi
