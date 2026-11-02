"""Platform-independent assistant core shared by every Hermes messaging channel.

Telegram, Zalo and later channels reach the same agent loop; this package
holds what must behave identically on all of them: per-person daily quota,
tool policy, and per-API-call telemetry for the SCER-AH router research.
It never imports a platform adapter. ``hermes_plugin`` wires it into Hermes
through lifecycle hooks only.
"""
