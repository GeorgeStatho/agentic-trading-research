from __future__ import annotations

import ast
import unittest

from scripts.codemods.late_bind_db_path import _drop_from_list, _Source, _transform


class LateBindDbPathCodemodTests(unittest.TestCase):
    def assertTransforms(self, source: str, expected: str) -> None:
        self.assertEqual(_transform(source), expected)
        self.assertEqual(_transform(expected), expected)

    def test_function_local_import_is_dropped_when_its_function_no_longer_uses_it(self) -> None:
        self.assertTransforms(
            "def load():\n"
            "    from db_common import DB_PATH, get_connection\n"
            "    initialize(db_path=DB_PATH)\n"
            "    return get_connection(DB_PATH)\n"
            "\n"
            "def only():\n"
            "    from db_common import DB_PATH\n"
            "\n"
            "def kept():\n"
            "    from db_common import DB_PATH\n"
            "    return str(DB_PATH)\n",
            "def load():\n"
            "    from db_common import get_connection\n"
            "    initialize()\n"
            "    return get_connection()\n"
            "\n"
            "def only():\n"
            "    pass\n"
            "\n"
            "def kept():\n"
            "    from db_common import DB_PATH\n"
            "    return str(DB_PATH)\n",
        )

    def test_top_level_import_stays_while_any_function_uses_the_name(self) -> None:
        source = (
            "from db_common import DB_PATH, get_connection\n"
            "def f(db_path=DB_PATH):\n"
            "    print(DB_PATH)\n"
        )
        self.assertEqual(
            _transform(source),
            "from db_common import DB_PATH, get_connection\n"
            "def f(db_path=None):\n"
            "    print(DB_PATH)\n",
        )

    def test_db_common_attribute_form_is_rewritten_like_the_bare_name(self) -> None:
        self.assertTransforms(
            "import db_common\n"
            "from dataclasses import dataclass\n"
            "from pathlib import Path\n"
            "\n"
            "@dataclass\n"
            "class Config:\n"
            "    db_path: Path = db_common.DB_PATH\n"
            "\n"
            "def f(db_path: Path | str = db_common.DB_PATH, *, other=db_common.DB_PATH):\n"
            "    g(1, db_path=db_common.DB_PATH)\n"
            "    return db_common.get_connection(db_common.DB_PATH)\n",
            "import db_common\n"
            "from dataclasses import dataclass\n"
            "from pathlib import Path\n"
            "\n"
            "@dataclass\n"
            "class Config:\n"
            "    db_path: Path | None = None\n"
            "\n"
            "def f(db_path: Path | str | None = None, *, other=None):\n"
            "    g(1)\n"
            "    return db_common.get_connection()\n",
        )

    def test_drops_next_to_each_other_do_not_overlap(self) -> None:
        self.assertTransforms(
            "f(get_connection(DB_PATH), db_path=DB_PATH)\n"
            "f(db_path=DB_PATH, x=get_connection(DB_PATH))\n"
            "from db_common import (\r\n"
            "    DB_PATH,\r\n"
            "    get_connection,\r\n"
            ")\r\n",
            "f(get_connection())\n"
            "f(x=get_connection())\n"
            "from db_common import (\r\n"
            "    get_connection,\r\n"
            ")\r\n",
        )

    def test_consecutive_drops_in_one_list_merge_into_one_span(self) -> None:
        text = "f(a, b, c, d)\n"
        call = ast.parse(text).body[0].value
        spans = _drop_from_list(_Source(text), call.args, {1, 2})
        self.assertEqual(spans, [(3, 9)])
        start, end = spans[0]
        self.assertEqual(text[:start] + text[end:], "f(a, d)\n")
        spans = _drop_from_list(_Source(text), call.args, {0, 1})
        self.assertEqual([text[s:e] for s, e in spans], ["a, b, "])


if __name__ == "__main__":
    unittest.main()
