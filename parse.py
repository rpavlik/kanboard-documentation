#!/usr/bin/env python3

from pathlib import Path
from pprint import pprint
from typing import Optional
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.front_matter import front_matter_plugin
import caseswitcher
import enum


class State(enum.Enum):
    NORMAL = 0
    IN_HEADING = 1
    EXPECT_PARAM_LIST = 2
    IN_PARAM_LIST = 3


class ExtractorStateMachine:

    def __init__(self) -> None:
        self.state = State.NORMAL
        self.lines: list[str] = []
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
        text_nodes = [child.content for child in t.children if child.type == "text"]

        param_name = text_nodes[1]
        type_info = "".join(text_nodes[2:])
        is_optional = "optional" in type_info
        param_type = "Any"
        default = ""

        # print(len(text_nodes))
        if "string" in type_info:
            param_type = "str"
        elif "integer" in type_info or "int," in type_info:
            param_type = "int"
        elif "boolean" in type_info:
            param_type = "bool"
            # if "default is false" in type_info:
            #     default = " = False"
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
        if self.state == State.NORMAL:
            if t.type == "heading_open":
                self.state = State.IN_HEADING
                return
            if t.content == "Parameters:":
                self.state = State.EXPECT_PARAM_LIST
                return
            # self.method = None
        elif self.state == State.IN_HEADING:
            if t.type == "heading_close":
                self.state = State.NORMAL
                return

            self.method = caseswitcher.to_snake(t.content)
            anchor = t.content.lower()
            self.url = f"https://docs.kanboard.org/v1/api/{stem}/#{anchor}"

        elif self.state == State.EXPECT_PARAM_LIST:
            if t.type == "bullet_list_open":
                self.state = State.IN_PARAM_LIST
                self.params: list[str] = []
                return
        elif self.state == State.IN_PARAM_LIST:
            if t.type == "inline":
                self._handle_param(t)
            elif t.type == "bullet_list_close":
                # done with params
                args = ""
                if self.params:
                    args = "*, " + ", ".join(self.params)

                self.lines.extend(
                    [
                        # f"\n{self.indent}# {self.url}",
                        f"\n{self.indent}def {self.method}(self, {args}): ...",
                        f'{self.indent}"""{self.url}"""',
                        # f"\n{self.indent}# {self.url}",
                        f"\n{self.indent}def {self.method}_async(self, {args}): ...",
                        f'{self.indent}"""{self.url}"""',
                    ]
                )
                self.state = State.NORMAL

    def handle_file_contents(self, md: MarkdownIt, stem: str, contents: str):

        tokens = md.parse(contents)

        for t in tokens:
            self.handle_token(stem, t)

    def inject_comment(self, comment_text):

        self.lines.append(f"\n{self.indent}# {comment_text}")


# def _parse_file(md: MarkdownIt, fn):
#     lines = []
#     with open(fn, "r", encoding="utf-8") as fp:
#         contents = fp.read()

#     pprint(tokens)

#     in_heading = False
#     next_list_parameters = False
#     for t in tokens:
#         # # find first level headings
#         # if t.level != 1:
#         #

#         if t.type == "heading_open":
#             in_heading = True

#         elif t.type == "heading_close":
#             in_heading = False

#         elif in_heading:

#             # next_is_header = False
#             # if not t.content:
#             #     continue
#             # if "example" in t.content:
#             #     continue

#             method = caseswitcher.to_snake(t.content)

#             lines.extend(
#                 [
#                     f"  def {method}(self, **kwargs): ...",
#                     f"  def {method}_async(self, **kwargs): ...",
#                 ]
#             )
#         elif t.content == "Parameters:":

#         # print("\n".join(lines))
#     return lines


if __name__ == "__main__":

    md = MarkdownIt("commonmark", {"breaks": True, "html": True}).use(
        front_matter_plugin
    )
    # _parse_file(md, "content/en/v1/api/project_procedures.md")

    sm = ExtractorStateMachine()
    # methods = []
    for fn in Path("content/en/v1/api/").glob("*_procedures.md"):
        section = fn.stem
        sm.inject_comment(section)

        with open(fn, "r", encoding="utf-8") as fp:
            contents = fp.read()
        sm.handle_file_contents(md, fn.stem, contents)
        # methods.extend(_parse_file(md, fn))

    with open("stubs.pyi", "w", encoding="utf-8") as fp:
        fp.write("class Client:\n")
        fp.write("\n".join(sm.lines))
        fp.write("\n")
    # print()
    # print("\n".join(sm.lines))
