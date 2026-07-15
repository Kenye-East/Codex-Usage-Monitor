# Codex-Only Monitor Design

This is a separate project directory and does not alter `Codex-Claude-Usage-Monitor`. It monitors Codex local usage logs only and permanently displays Codex; no Claude provider, credentials access, CLI invocation, or provider toggle remains.

The public reset-message feature remains Codex-only. It fetches `thsottiaux` through the existing Nitter feed once at startup, keeps unread state in the local configuration file, and renders a message icon plus badge. Each fixed-size card has no source icon, clamps preview text to two lines, shows its time on the bottom line, hides its tooltip before opening X, and uses the Codex pale-green palette.
