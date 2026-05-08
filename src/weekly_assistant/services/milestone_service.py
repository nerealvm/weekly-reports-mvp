import re


DATE_PATTERN = re.compile(r"\b([0-9]{1,2}[./][0-9]{1,2}(?:[./][0-9]{2,4})?)\b")


def extract_first_milestone_date(text: str) -> str:
    match = DATE_PATTERN.search(text or "")
    return match.group(1) if match else ""


def carry_forward_milestone(previous_milestone: str) -> tuple[str, str]:
    milestone = (previous_milestone or "").strip()
    return milestone, extract_first_milestone_date(milestone)
