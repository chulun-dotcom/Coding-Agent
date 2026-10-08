"""Create five fixed coding tasks with separate acceptance tests."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

CASES = [
    {
        "name": "pagination-negative-page",
        "instruction": "让 page 小于 1 时抛出 ValueError，保留正常分页行为，并补充测试。",
        "files": {
            "pagination.py": "def paginate(items, page, page_size):\n    start = (page - 1) * page_size\n    return items[start:start + page_size]\n",
            "test_pagination.py": "from pagination import paginate\n\ndef test_first_page():\n    assert paginate([1, 2, 3], 1, 2) == [1, 2]\n",
        },
        "acceptance": "from pagination import paginate\nimport pytest\n\ndef test_negative_page():\n    with pytest.raises(ValueError):\n        paginate([1, 2, 3], -1, 2)\n",
    },
    {
        "name": "average-empty-input",
        "instruction": "让 average([]) 抛出 ValueError，正常输入仍返回平均值，并补充测试。",
        "files": {
            "average.py": "def average(values):\n    return sum(values) / len(values)\n",
            "test_average.py": "from average import average\n\ndef test_numbers():\n    assert average([2, 4]) == 3\n",
        },
        "acceptance": "from average import average\nimport pytest\n\ndef test_empty():\n    with pytest.raises(ValueError):\n        average([])\n",
    },
    {
        "name": "slug-whitespace",
        "instruction": "让 slugify 将连续空白合并为一个连字符，去掉两端空白，并补充测试。",
        "files": {
            "slug.py": "def slugify(value):\n    return value.lower().replace(' ', '-')\n",
            "test_slug.py": "from slug import slugify\n\ndef test_simple():\n    assert slugify('Hello World') == 'hello-world'\n",
        },
        "acceptance": "from slug import slugify\n\ndef test_mixed_whitespace():\n    assert slugify('  Hello  \\tWorld  ') == 'hello-world'\n",
    },
    {
        "name": "divide-zero",
        "instruction": "让 divide(a, 0) 抛出 ValueError，保留正常除法，并补充测试。",
        "files": {
            "division.py": "def divide(a, b):\n    return a / b\n",
            "test_division.py": "from division import divide\n\ndef test_normal():\n    assert divide(8, 2) == 4\n",
        },
        "acceptance": "from division import divide\nimport pytest\n\ndef test_zero_denominator():\n    with pytest.raises(ValueError):\n        divide(8, 0)\n",
    },
    {
        "name": "unique-preserve-order",
        "instruction": "让 unique(items) 去重时保留首次出现的顺序，并补充测试。",
        "files": {
            "unique.py": "def unique(items):\n    return list(set(items))\n",
            "test_unique.py": "from unique import unique\n\ndef test_members():\n    assert set(unique([1, 2, 1])) == {1, 2}\n",
        },
        "acceptance": "from unique import unique\n\ndef test_order():\n    assert unique([3, 1, 2, 1, 3]) == [3, 1, 2]\n",
    },
]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    data_root = project / "data"
    fixture_root = data_root / "evaluation-repos" / uuid.uuid4().hex[:8]
    fixture_root.mkdir(parents=True)
    manifest_path = data_root / "evaluations" / "dev.jsonl"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for case in CASES:
        source = fixture_root / case["name"] / "source"
        hidden = fixture_root / case["name"] / "acceptance"
        source.mkdir(parents=True)
        hidden.mkdir(parents=True)
        write(source / ".gitignore", "__pycache__/\n.pytest_cache/\n*.pyc\n")
        for name, content in case["files"].items():
            write(source / name, content)
        write(hidden / "test_acceptance.py", case["acceptance"])
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Evaluation",
                "-c",
                "user.email=evaluation@example.com",
                "commit",
                "-q",
                "-m",
                "baseline",
            ],
            check=True,
        )
        commit = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        records.append(
            {
                "name": case["name"],
                "repo": Path(os.path.relpath(source, manifest_path.parent)).as_posix(),
                "acceptance": Path(os.path.relpath(hidden, manifest_path.parent)).as_posix(),
                "base_commit": commit,
                "instruction": case["instruction"],
                "profile": "python",
                "max_steps": 25,
            }
        )
    manifest_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
        newline="\n",
    )
    print(manifest_path)
    print(f"prepared {len(records)} tasks")


if __name__ == "__main__":
    main()
