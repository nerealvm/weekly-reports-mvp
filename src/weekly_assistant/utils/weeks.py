from datetime import date, timedelta


def current_week_bounds(today: date | None = None) -> tuple[date, date]:
    """Friday-to-Friday week. End is the upcoming Friday, or today when today is Friday.

    Sheet columns are labelled by their end-Friday, so the end date must land on a
    Friday or write-back targets a week column that does not exist yet.
    """
    today = today or date.today()
    days_to_friday = (4 - today.weekday()) % 7
    end = today + timedelta(days=days_to_friday)
    return end - timedelta(days=7), end
