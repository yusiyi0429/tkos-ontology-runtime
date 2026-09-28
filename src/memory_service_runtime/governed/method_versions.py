"""Method contract versions, oldest first.

Imports nothing, so every governed module can use it without an import cycle.
A new Method version is appended to ``METHOD_VERSIONS`` and so joins every
"since" family below; a version that must not inherit a family's behaviour
needs its own explicit check. ``protocol.SUPPORTED_PROTOCOL_CONTRACTS`` stays
the authority on what this build supports (a test keeps the two equal).
"""

METHOD_VERSIONS = ("tkos.method/0.1", "tkos.method/0.2", "tkos.method/0.3", "tkos.method/0.4", "tkos.method/0.5")
SINCE_V02 = METHOD_VERSIONS[1:]
SINCE_V03 = METHOD_VERSIONS[2:]
# 0.4 起共用同一套正式治理机制（全体确认、整组激活、scoped 授权）。
FORMAL_GOVERNANCE_VERSIONS = frozenset(METHOD_VERSIONS[3:])
