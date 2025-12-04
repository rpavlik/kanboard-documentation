#!/usr/bin/env python3
# Copyright 2025, The Khronos Group Inc.
#
# SPDX-License-Identifier: MIT

from dataclasses import dataclass
import enum
from pathlib import Path
import pprint
from typing import Optional
from copy import deepcopy
import json

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

# JSON schemas for python types
_SCHEMAS = {
    # "Any": {"$comment": "Any"},
    "list[str]": {"type": "array", "additionalItems": {"type": "string"}},
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "bool": {"type": "boolean"},
    "dict": {"type": "object"},
    "list": {"type": "array", "additionalItems": True},
}


class State(enum.Enum):
    NORMAL = 0
    IN_HEADING = 1
    EXPECT_PARAM_LIST = 2
    IN_PARAM_LIST = 3
    EXPECT_TITLE = 4
    IN_RESULT_SUCCESS = 5
    IN_RESULT_FAILURE = 6
    IN_PURPOSE = 7


@dataclass
class Param:
    param_type: str
    param_name: str
    optional: bool
    summary: str

    def to_python(self):
        default = ""
        param_type = self.param_type
        if self.optional:
            param_type = f"Optional[{param_type}]"
            default = " = None"
        return f"{self.param_name}: {param_type}{default}"

    def to_jsonrpc(self):
        ret = {
            "name": self.param_name,
            "summary": self.summary,
            "schema": deepcopy(_SCHEMAS[self.param_type]),
        }
        if not self.optional:
            ret["required"] = True
        return ret


def _combine_schemas(success_schema: dict, failure_schema: dict):
    success_schema["description"] = "on success"
    failure_schema["description"] = "on failure"
    return {
        "oneOf": [
            success_schema,
            failure_schema,
        ]
    }


def _guess_return_schema(desc: str):
    if desc == "true":
        return {"enum": [True]}
    if desc == "false":
        return {"enum": [False]}
    if desc == "null":
        return {"enum": [None]}
    if desc.endswith("_id"):
        return {
            "title": desc,
            "type": "integer",
        }

    folded = desc.casefold()
    if desc == "[]" or folded == "empty array":
        return {"type": "array", "additionalItems": False}

    if folded == "empty string":
        return {"enum": [""]}

    if folded.startswith("list of"):
        # TODO recursively guess contents
        return {"title": desc, "type": "array", "additionalItems": True}

    if folded.startswith("dict"):
        # TODO recursively guess contents
        return {"title": desc, "type": "object", "additionalProperties": True}

    if "string" in folded:
        return {"title": desc, "type": "string"}
    # ran out of heuristics
    return {
        "title": desc,
        "$comment": "Could not guess json schema for this type",
    }


def _try_extract_prefixed(t: Token, prefix: str):
    if not t.children:
        return None
    if not t.content.startswith(prefix):
        return None

    # print(prefix, t.content)
    useful_texts = [
        c.content
        for c in t.children
        if c.type == "text" and not c.content.startswith(prefix)
    ]
    # print(useful_texts)
    return "".join(useful_texts)


class ExtractorStateMachine:

    def __init__(self) -> None:
        self.state = State.NORMAL
        self.lines: list[str] = [_FILE_START]
        self.method: Optional[str] = None
        self.params: list[Param] = []
        self.indent = "    "
        self.url = ""
        self.orig_method: str
        self.openrpc = {
            "openrpc": "1.2.1",
            "info": {"version": "1.2", "title": "Kanboard"},
            "methods": [],
        }

        self.result = ""
        self.on_success = ""
        self.on_failure = ""
        self.purpose = ""

    def _handle_param_children(self, parent_content: str, children: list[Token]):
        text_nodes = [c.content for c in children if c.type == "text" and c.content]

        if not text_nodes:
            return

        param_name = text_nodes[0]
        if param_name == "none":
            return

        type_info = "".join(text_nodes[1:])
        is_optional = "optional" in type_info
        param_type = "Any"

        if "[]string" in type_info:
            param_type = "list[str]"
        elif "string" in type_info:
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
                f"'{parent_content}'",
            )

        self.params.append(
            Param(
                param_name=param_name,
                param_type=param_type,
                optional=is_optional,
                summary=type_info.strip(),
            )
        )

    def _handle_param(self, t: Token):
        if not t.children:
            # self.params = ["**kwargs"]
            # return
            raise RuntimeError("Unexpected case")
        if t.content == "**none**":
            self.params = []
            return

        self._handle_param_children(t.content, t.children)

    def _finish_method(self):
        args = ""
        if self.params:
            python_params = [param.to_python() for param in self.params]
            args = "*, " + ", ".join(python_params)
        method = self.method
        indent = self.indent
        self.lines.extend(
            [
                "",
                f"{indent}def {method}(self, {args}): ...",
                f'{indent}"""{self.url}"""',
                "",
                f"{indent}async def {method}_async(self, {args}): ...",
                f'{indent}"""{self.url}"""',
            ]
        )
        rpc_method = {
            "name": self.orig_method,
            "params": [param.to_jsonrpc() for param in self.params],
            "result": {
                # todo
                "name": "retval",
                "schema": {},
            },
            "externalDocs": {"url": self.url},
        }
        if self.purpose:
            rpc_method["summary"] = self.purpose

        if self.result:
            rpc_method["result"]["schema"] = _guess_return_schema(self.result)

        elif self.on_success:
            on_success = _guess_return_schema(self.on_success)
            on_failure = _guess_return_schema(self.on_failure)
            rpc_method["result"]["schema"] = _combine_schemas(
                success_schema=on_success, failure_schema=on_failure
            )

        self.openrpc["methods"].append(rpc_method)

        # reset state machine
        self.state = State.NORMAL
        self.result = ""
        self.on_success = ""
        self.on_failure = ""
        self.purpose = ""
        self.params = []

    def handle_token(self, stem: str, t: Token):
        # State.NORMAL
        if self.state == State.NORMAL:
            if t.type == "heading_open":
                self.state = State.IN_HEADING
                return

            if t.content == "Parameters:":
                self.state = State.EXPECT_PARAM_LIST
                return

            if t.content == "Parameters: none":
                # no params
                # self._finish_method()
                return

            if t.content.startswith("Parameters:") and t.children:
                # skip the first child, which is the word "Parameters: ",
                # and treat the rest like a param bullet point
                self._handle_param_children(t.content, t.children[1:])
                return

            purpose = _try_extract_prefixed(t, "Purpose:")
            if purpose:
                self.purpose = purpose
                return

            result = _try_extract_prefixed(t, "Result:")
            if result:
                self.result = result
                return
            on_success = _try_extract_prefixed(t, "Result on success:")
            if on_success:
                self.on_success = on_success
                return

            on_failure = _try_extract_prefixed(t, "Result on failure:")
            if on_failure:
                self.on_failure = on_failure
                return

            # if t.type == "text":
            #     content = t.content.strip()
            #     if content == "Purpose:":
            #         self.state = State.IN_PURPOSE
            #         return
            #     if content == "Result on success:":
            #         self.state = State.IN_RESULT_SUCCESS
            #         return
            #     if content == "Result on failure:":
            #         self.state = State.IN_RESULT_FAILURE
            #         return

            if t.type == "bullet_list_close":
                self._finish_method()

        # State.IN_HEADING
        elif self.state == State.IN_HEADING:
            if t.type == "heading_close":
                self.state = State.NORMAL
                return

            self.orig_method = t.content
            self.method = caseswitcher.to_snake(t.content)
            anchor = t.content.lower()
            self.url = f"https://docs.kanboard.org/v1/api/{stem}/#{anchor}"

        # State.EXPECT_PARAM_LIST
        elif self.state == State.EXPECT_PARAM_LIST:
            if t.type == "bullet_list_open":
                self.state = State.IN_PARAM_LIST
                return

        # State.IN_PARAM_LIST
        elif self.state == State.IN_PARAM_LIST:
            if t.type == "inline":
                self._handle_param(t)

            elif t.type == "bullet_list_close":
                # done with params
                self.state = State.NORMAL

        elif self.state == State.IN_RESULT_FAILURE:
            if t.type == "text":
                self.on_failure = t.content
                print("failure", self.on_failure)
                self.state = State.NORMAL
                return
            if t.type == "paragraph_close":
                raise RuntimeError("Didn't find contents for failure")

        elif self.state == State.IN_RESULT_SUCCESS:
            if t.type == "text":
                self.on_success = t.content
                print("success", self.on_success)
                self.state = State.NORMAL
                return
            if t.type == "paragraph_close":
                raise RuntimeError("Didn't find contents for success")

        elif self.state == State.IN_PURPOSE:
            if t.type == "text":
                self.purpose = t.content
                print("purpose", self.purpose)
                self.state = State.NORMAL
                return
            if t.type == "paragraph_close":
                raise RuntimeError("Didn't find contents for purpose")

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

    with open("rpc.json", "w", encoding="utf-8") as fp:
        json.dump(sm.openrpc, fp, indent=2)
