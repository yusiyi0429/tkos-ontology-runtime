"""Create the isolated database used by the dashboard 0.3 acceptance.

Delegates to the anchors_v03 helper: a fresh `tkos_a1_method_*` database in the
existing local PostgreSQL container, migrated through 0025 with role-separated
app/owner identities. Existing databases, containers and credentials are never
reset or modified.
"""
from acceptance.anchors_v03.database import main

if __name__ == "__main__":
    main()
