WEEKLY_ROW_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "result_text",
        "movement_type",
        "next_milestone",
        "next_milestone_date",
        "ball_side",
        "open_question_to_evgeny",
        "needs_sync",
        "sync_reason",
    ],
    "properties": {
        "result_text": {"type": "string"},
        "movement_type": {"type": "string", "enum": ["real_result", "no_movement", "unclear"]},
        "next_milestone": {"type": "string"},
        "next_milestone_date": {"type": "string"},
        "ball_side": {"type": "string"},
        "open_question_to_evgeny": {"type": "string"},
        "needs_sync": {"type": "string", "enum": ["yes", "no"]},
        "sync_reason": {"type": "string"},
    },
}
