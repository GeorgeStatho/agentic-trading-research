import json
from pathlib import Path

def generalWrite(data: dict, key: str, path: Path):
    data_id = data.get(key)

    if not data_id:
        return

    dict_data: dict[str, dict] = {}
    if path.exists():
        try:
            existing_data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing_data, dict):
                dict_data = existing_data
        except json.JSONDecodeError:
            dict_data = {}

    dict_data[data_id] = data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict_data, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(dict_data[data_id], indent=2, sort_keys=True))


