"""The AI agent's voice worker (M8): one conversation per call, over Asterisk's WebSocket media.

``core`` decides (pure: turn detection, handoff rules, the conversation); ``adapters`` talk to
OpenAI, Asterisk and Redis; ``service`` runs one session per call. Started by ``manage.py run_ai``.
"""
