import json
from pathlib import Path

input_file = Path(
    "/run/media/harsh/9f6ca1b8-d7a8-43e2-a13b-1c0510a6b53c/Projects/velma/backend/txts/test-1.txt"
)
output_file = Path(
    "/run/media/harsh/9f6ca1b8-d7a8-43e2-a13b-1c0510a6b53c/Projects/velma/backend/txts/test-1.json"
)

article = input_file.read_text(encoding="utf-8")

payload = {"article": article}

output_file.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
)

print(f"Created: {output_file}")
