"""DB/network-touching orchestration that sits above the pure engines in
app/engines/ — unlike those (zero I/O, fully unit-testable in isolation),
modules here talk to the database and/or external channels (WebSocket,
notifications). Kept separate so "is this function pure?" stays a
one-glance answer by directory alone.
"""
