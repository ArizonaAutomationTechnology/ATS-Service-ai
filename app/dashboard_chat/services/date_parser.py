import datetime
import re
from typing import Tuple, Optional

def parse_date_range(text: str, base_date: Optional[datetime.date] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Parses common natural language date terms into (startDate, endDate) format.
    Returns strings in YYYY-MM-DD format.
    """
    if not base_date:
        base_date = datetime.date.today()
    
    text = text.lower().strip()
    
    months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
    months_short = [m[:3] for m in months]

    
    def fmt(d: datetime.date) -> str:
        return d.strftime("%Y-%m-%d")
        
    # today
    if text == "today":
        return fmt(base_date), fmt(base_date)
        
    # yesterday
    if text == "yesterday":
        y = base_date - datetime.timedelta(days=1)
        return fmt(y), fmt(y)
        
    # this week (Monday to Sunday)
    if text == "this week":
        start = base_date - datetime.timedelta(days=base_date.weekday())
        end = start + datetime.timedelta(days=6)
        return fmt(start), fmt(end)
        
    # last week
    if text == "last week":
        this_week_start = base_date - datetime.timedelta(days=base_date.weekday())
        start = this_week_start - datetime.timedelta(days=7)
        end = start + datetime.timedelta(days=6)
        return fmt(start), fmt(end)
        
    # this month
    if text == "this month":
        start = base_date.replace(day=1)
        # Find next month first day then subtract one day
        next_month = base_date.replace(day=28) + datetime.timedelta(days=4)
        end = next_month.replace(day=1) - datetime.timedelta(days=1)
        return fmt(start), fmt(end)
        
    # last month
    if text == "last month":
        first_of_this = base_date.replace(day=1)
        end = first_of_this - datetime.timedelta(days=1)
        start = end.replace(day=1)
        return fmt(start), fmt(end)
        
    # this year
    if text == "this year":
        start = datetime.date(base_date.year, 1, 1)
        end = datetime.date(base_date.year, 12, 31)
        return fmt(start), fmt(end)
        
    # last year
    if text == "last year":
        start = datetime.date(base_date.year - 1, 1, 1)
        end = datetime.date(base_date.year - 1, 12, 31)
        return fmt(start), fmt(end)
        
    # last N days
    m = re.match(r"last\s+(\d+)\s+days?", text)
    if m:
        n = int(m.group(1))
        start = base_date - datetime.timedelta(days=n)
        return fmt(start), fmt(base_date)
        
    # Range of dates: "from 1 July to 15 July" or "from 1 July 2026 to 15 July 2026"
    range_match = re.search(
        r"(?:from\s+)?(\d+)\s+([a-z]{3,})\s*(\d{4})?\s+(?:to|and|-)\s+(\d+)\s+([a-z]{3,})\s*(\d{4})?", 
        text
    )
    if range_match:
        d1_str, m1_str, y1_str, d2_str, m2_str, y2_str = range_match.groups()
        try:
            def parse_m(m_str: str) -> int:
                m_str = m_str.lower()
                for idx, m_full in enumerate(months):
                    if m_full.startswith(m_str[:3]):
                        return idx + 1
                raise ValueError()
            
            m1 = parse_m(m1_str)
            m2 = parse_m(m2_str)
            
            y1 = int(y1_str) if y1_str else base_date.year
            y2 = int(y2_str) if y2_str else y1
            
            start = datetime.date(y1, m1, int(d1_str))
            end = datetime.date(y2, m2, int(d2_str))
            return fmt(start), fmt(end)
        except Exception:
            pass
            
    # Simple range: "1 jul to 15 jul"
    range_short_match = re.search(r"(\d+)\s+([a-z]{3})\s*(?:to|and|-)\s*(\d+)\s+([a-z]{3})", text)
    if range_short_match:
        d1_str, m1_str, d2_str, m2_str = range_short_match.groups()
        try:
            def parse_m_short(m_str: str) -> int:
                return months_short.index(m_str[:3]) + 1
            start = datetime.date(base_date.year, parse_m_short(m1_str), int(d1_str))
            end = datetime.date(base_date.year, parse_m_short(m2_str), int(d2_str))
            return fmt(start), fmt(end)
        except Exception:
            pass

    # check if text matches month name (this is a fallback for single month queries)
    month_pattern = r"\b(" + "|".join(months + months_short) + r")\b"
    m_my = re.search(month_pattern, text)
    if m_my:
        month_name = m_my.group(1)
        if month_name in months:
            m_idx = months.index(month_name) + 1
        else:
            m_idx = months_short.index(month_name) + 1
            
        m_yr = re.search(r"\b(20\d{2})\b", text)
        year = int(m_yr.group(1)) if m_yr else base_date.year
        
        start = datetime.date(year, m_idx, 1)
        next_m_year = year if m_idx < 12 else year + 1
        next_m_idx = m_idx + 1 if m_idx < 12 else 1
        end = datetime.date(next_m_year, next_m_idx, 1) - datetime.timedelta(days=1)
        return fmt(start), fmt(end)
            
    return None, None

