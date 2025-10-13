#!/usr/bin/env python3
# Copyright 2025, The Khronos Group Inc.
#
# SPDX-License-Identifier: MIT

import enum
from pathlib import Path
from typing import Optional

import caseswitcher
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.front_matter import front_matter_plugin

_FILE_START = """
# Copyright (c) 2014-2023 Frédéric Guillot
# Copyright 2025, The Khronos Group Inc.
#
# SPDX-License-Identifier: MIT
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import asyncio
from typing import Optional

DEFAULT_AUTH_HEADER = "Authorization"

class Client:
    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        auth_header: str = DEFAULT_AUTH_HEADER,
        cafile: Optional[str] = None,
        insecure: bool = False,
        ignore_hostname_verification: bool = False,
        user_agent: str = "Kanboard Python API Client",
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None: ...

"""


class State(enum.Enum):
    NORMAL = 0
    IN_HEADING = 1
    EXPECT_PARAM_LIST = 2
    IN_PARAM_LIST = 3


class ExtractorStateMachine:

    def __init__(self) -> None:
        self.state = State.NORMAL
        self.lines: list[str] = [_FILE_START]
        self.method: Optional[str] = None
        self.params: list[str] = []
        self.indent = "    "
        self.url = ""

    def _handle_param(self, t: Token):
        if not t.children:
            self.params = ["**kwargs"]
            return
        if t.content == "**none**":
            self.params = []
            return
        text_nodes = [c.content for c in t.children if c.type == "text"]

        param_name = text_nodes[1]
        type_info = "".join(text_nodes[2:])
        is_optional = "optional" in type_info
        param_type = "Any"
        default = ""

        if "string" in type_info:
            param_type = "str"
        elif "integer" in type_info or "int," in type_info:
            param_type = "int"
        elif "boolean" in type_info:
            param_type = "bool"
        elif "dict" in type_info or "key/value" in type_info:
            param_type = "dict"
        elif "array" in type_info:
            param_type = "list"
        else:
            print(
                "Could not figure out param type:",
                f"'{type_info}'",
                "from parent node",
                f"'{t.content}'",
            )
        if is_optional:
            param_type = f"Optional[{param_type}]"
            default = " = None"

        self.params.append(f"{param_name}: {param_type}{default}")

    def handle_token(self, stem: str, t: Token):
        # State.NORMAL
        if self.state == State.NORMAL:
            if t.type == "heading_open":
                self.state = State.IN_HEADING
                return

            if t.content == "Parameters:":
                self.state = State.EXPECT_PARAM_LIST
                return

        # State.IN_HEADING
        elif self.state == State.IN_HEADING:
            if t.type == "heading_close":
                self.state = State.NORMAL
                return

            self.method = caseswitcher.to_snake(t.content)
            anchor = t.content.lower()
            self.url = f"https://docs.kanboard.org/v1/api/{stem}/#{anchor}"

        # State.EXPECT_PARAM_LIST
        elif self.state == State.EXPECT_PARAM_LIST:
            if t.type == "bullet_list_open":
                self.state = State.IN_PARAM_LIST
                self.params: list[str] = []
                return

        # State.IN_PARAM_LIST
        elif self.state == State.IN_PARAM_LIST:
            if t.type == "inline":
                self._handle_param(t)

            elif t.type == "bullet_list_close":
                # done with params: let's accumulate the new call signature
                args = ""
                if self.params:
                    args = "*, " + ", ".join(self.params)
                method = self.method
                indent = self.indent
                self.lines.extend(
                    [
                        "",
                        f"{indent}def {method}(self, {args}): ...",
                        f'{indent}"""{self.url}"""',
                        "",
                        f"{indent}def {method}_async(self, {args}): ...",
                        f'{indent}"""{self.url}"""',
                    ]
                )
                self.state = State.NORMAL

    def handle_file_contents(self, md: MarkdownIt, stem: str, contents: str):
        tokens = md.parse(contents)

        for t in tokens:
            self.handle_token(stem, t)

    def inject_comment(self, comment_text):
        self.lines.append(f"\n{self.indent}# {comment_text}")


if __name__ == "__main__":

    md = MarkdownIt("commonmark", {"breaks": True, "html": True}).use(
        front_matter_plugin
    )

    sm = ExtractorStateMachine()
    for fn in Path("content/en/v1/api/").glob("*_procedures.md"):
        section = fn.stem
        sm.inject_comment(section)

        with open(fn, "r", encoding="utf-8") as fp:
            contents = fp.read()
        sm.handle_file_contents(md, fn.stem, contents)

    with open("stubs.pyi", "w", encoding="utf-8") as fp:
        fp.write("\n".join(sm.lines))
        fp.write("\n")
