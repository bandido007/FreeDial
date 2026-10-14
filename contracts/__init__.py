"""Messages shared by web, brain and writer.

Commands (web → brain) and events (brain → writer) travel on Redis Streams as
JSON built by :mod:`contracts.envelope`. This package imports nothing of ours
and nothing of Django, so both sides can depend on it.
"""
