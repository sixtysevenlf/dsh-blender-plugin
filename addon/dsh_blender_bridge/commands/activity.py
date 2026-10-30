""" Activity state and human activity commands. """

from ..protocol import _ai_paused
from ..recorders.activity import get_activity_tracker
from ..recorders.user_edit import get_edit_recorder

class ActivityCommands:
    def get_activity_state(self):
        """v1.8：统一 activity 状态（human / agent / idle / both）+ 原生选区摘要。"""
        try:
            # 查询命令不能把自己算成 agent（否则每次查询都回 state=agent）
            snap = get_activity_tracker().snapshot(include_current_command=False)
            snap["paused"] = _ai_paused()
            return snap
        except Exception as e:
            print(f"Error getting activity state: {str(e)}")
            return {"error": str(e)}

    def drain_human_activity(self):
        """Return human-originated events buffered since the last drain.

        Consent is enforced MCP-side (the server only drains and uploads when
        the user has opted in), but we also refuse here so a buffer does not
        accumulate for a user who has said no.
        """
        try:
            if not self.get_telemetry_consent().get("consent"):
                get_edit_recorder().drain()
                return {"events": []}
            return {"events": get_edit_recorder().drain()}
        except Exception as e:
            print(f"Error draining manual edits: {str(e)}")
            return {"error": str(e)}
