""" Agent-command suppression shared by the local activity tracker. """

from contextlib import contextmanager


class AgentCommandGate:
    """Marks the window in which an agent command is executing.

    Human activity detection must not attribute agent side effects to the
    human, so the tracker suppresses notifications while this context is held.
    No events are recorded, buffered or persisted.
    """

    def __init__(self):
        self._agent_depth = 0

    @contextmanager
    def agent_command(self):
        self._agent_depth += 1
        try:
            yield
        finally:
            self._agent_depth = max(0, self._agent_depth - 1)

    @property
    def _suppressed(self):
        return self._agent_depth > 0


edit_recorder = AgentCommandGate()


def get_edit_recorder():
    return edit_recorder
