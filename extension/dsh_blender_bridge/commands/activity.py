""" Activity state command. """

from ..protocol import ai_paused
from ..recorders.activity import get_activity_tracker

class ActivityCommands:
    def get_activity_state(self):
        """v1.8：统一 activity 状态（human / agent / idle / both）+ 原生选区摘要。"""
        try:
            # 查询命令不能把自己算成 agent（否则每次查询都回 state=agent）
            snap = get_activity_tracker().snapshot(include_current_command=False)
            snap["paused"] = ai_paused()
            return snap
        except Exception as e:
            print(f"Error getting activity state: {str(e)}")
            return {"error": str(e)}
