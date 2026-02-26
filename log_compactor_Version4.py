from datetime import datetime, timedelta
from typing import Generator, Optional
from collections import defaultdict
import sys


def compact_logs(
    file_path: str,
    dedup_window_seconds: int,
    error_threshold: int,
) -> Generator[str, None, None]:
    """
    Reads logs from file_path and returns a generator of compacted log strings.
    
    Handles deduplication within time windows, error escalation, code-based
    level override, field normalization, and proper output ordering.
    """
    
    def parse_timestamp(ts: str) -> Optional[datetime]:
        """Parse ISO-8601 timestamp without timezone."""
        try:
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return None
    
    def parse_fields(parts: list[str]) -> Optional[dict]:
        """Parse key=value fields from log line. Returns None if malformed."""
        fields = {}
        for part in parts:
            if '=' not in part:
                return None
            key, value = part.split('=', 1)
            fields[key] = value
        return fields
    
    def normalize_user_field(fields: dict) -> Optional[dict]:
        """
        Normalize user/user_id fields. Returns None if conflicting values.
        Always uses 'user' as canonical form.
        """
        user = fields.get('user')
        user_id = fields.get('user_id')
        
        if user_id is not None:
            if user is not None and user != user_id:
                return None
            fields = {k: v for k, v in fields.items() if k != 'user_id'}
            fields['user'] = user_id
        
        return fields
    
    def apply_code_override(level: str, fields: dict) -> str:
        """Apply error level override if code is 500-599."""
        if 'code' in fields:
            try:
                code = int(fields['code'])
                if 500 <= code <= 599:
                    return 'ERROR'
            except ValueError:
                pass
        return level
    
    def make_group_key(level: str, fields: dict) -> tuple:
        """Create hashable key for grouping logs by level and fields."""
        sorted_items = tuple(sorted(fields.items()))
        return (level, sorted_items)
    
    def format_timestamp_range(start: datetime, end: datetime) -> str:
        """Format timestamp range according to output spec."""
        start_str = start.isoformat()
        if start.date() == end.date():
            end_str = end.time().isoformat()
        else:
            end_str = end.isoformat()
        
        if start == end:
            return start_str
        return f"{start_str}~{end_str}"
    
    def format_output(
        start_ts: datetime,
        end_ts: datetime,
        level: str,
        fields: dict,
        count: int,
    ) -> str:
        """Format final output line."""
        ts_range = format_timestamp_range(start_ts, end_ts)
        sorted_fields = ' '.join(
            f"{k}={v}" for k, v in sorted(fields.items())
        )
        
        if count == 1:
            return f"{ts_range} {level} {sorted_fields}"
        else:
            return f"{ts_range} {level} {sorted_fields} (x{count})"
    
    logs: list[tuple[datetime, str, dict, int]] = []
    
    try:
        with open(file_path, 'r') as f:
            line_idx = 0
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                parts = line.split()
                if len(parts) < 2:
                    continue
                
                ts_str = parts[0]
                level = parts[1]
                
                ts = parse_timestamp(ts_str)
                if ts is None:
                    continue
                
                if not level or not level.isupper():
                    continue
                
                fields = parse_fields(parts[2:])
                if fields is None:
                    continue
                
                fields = normalize_user_field(fields)
                if fields is None:
                    continue
                
                level = apply_code_override(level, fields)
                
                logs.append((ts, level, fields, line_idx))
                line_idx += 1
    except (IOError, OSError):
        return
    
    groups: dict[tuple, list[tuple[datetime, str, dict, int]]] = defaultdict(list)
    
    for ts, level, fields, idx in logs:
        group_key = make_group_key(level, fields)
        groups[group_key].append((ts, level, fields, idx))
    
    compacted: list[tuple[datetime, int, str]] = []
    
    for group_key, group_logs in groups.items():
        level = group_key[0]
        
        i = 0
        while i < len(group_logs):
            window_start = group_logs[i][0]
            window_end = window_start + timedelta(seconds=dedup_window_seconds)
            
            window_group = [group_logs[i]]
            j = i + 1
            while j < len(group_logs) and group_logs[j][0] <= window_end:
                window_group.append(group_logs[j])
                j += 1
            
            start_ts = window_group[0][0]
            end_ts = window_group[-1][0]
            fields = window_group[0][2]
            count = len(window_group)
            first_idx = window_group[0][3]
            
            escalated = len(window_group) >= error_threshold and level == 'ERROR'
            output_level = 'CRITICAL' if escalated else level
            
            output = format_output(start_ts, end_ts, output_level, fields, count)
            compacted.append((start_ts, first_idx, output))
            
            i = j
    
    compacted.sort(key=lambda x: (x[0], x[1]))
    
    for _, _, output in compacted:
        yield output


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python log_compactor.py <file_path> <dedup_window_seconds> <error_threshold>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    dedup_window_seconds = int(sys.argv[2])
    error_threshold = int(sys.argv[3])
    
    for line in compact_logs(file_path, dedup_window_seconds, error_threshold):
        print(line)