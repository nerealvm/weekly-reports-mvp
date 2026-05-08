from weekly_assistant.domain.models import IncomingItem


def normalize_incoming_item(source_type: str, raw_text: str) -> IncomingItem:
    normalized = " ".join((raw_text or "").split())
    if not normalized:
        destination = "unclear"
        question = "Что именно нужно сделать или куда это отнести?"
    elif "евгений" in normalized.lower() or "weekly" in normalized.lower():
        destination = "weekly"
        question = ""
    elif "отдать володе" in normalized.lower():
        destination = "weekly"
        question = ""
    else:
        destination = "unclear"
        question = "Это weekly-тема, разовое поручение или личный контур?"
    return IncomingItem(
        source_type=source_type,
        raw_text=raw_text,
        normalized_text=normalized,
        proposed_destination=destination,
        clarification_question=question,
    )
