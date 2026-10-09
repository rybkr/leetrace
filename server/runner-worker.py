from __future__ import annotations

import contextlib
import __future__
import io
import json
import math
import resource
import signal
import sys
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Sequence, Set, TextIO, Union, cast

JsonValue = Union[None, bool, int, float, str, List['JsonValue'], Dict[str, 'JsonValue']]


class TreeNode:
    def __init__(self, val: int = 0, left: Optional[TreeNode] = None, right: Optional[TreeNode] = None) -> None:
        self.val = val
        self.left = left
        self.right = right


class ListNode:
    def __init__(self, val: int = 0, next: Optional[ListNode] = None) -> None:
        self.val = val
        self.next = next


def tree_node(values: JsonValue) -> Optional[TreeNode]:
    if not values:
        return None
    items = cast(List[Optional[int]], values)
    root = TreeNode(cast(int, items[0]))
    pending: Deque[TreeNode] = deque([root])
    index = 1
    while pending and index < len(items):
        node = pending.popleft()
        for attribute in ('left', 'right'):
            if index < len(items) and items[index] is not None:
                child = TreeNode(cast(int, items[index]))
                setattr(node, attribute, child)
                pending.append(child)
            index += 1
    return root


def list_node(value: JsonValue) -> Optional[ListNode]:
    cycle = -1
    if isinstance(value, dict):
        cycle = cast(int, value['cycle'])
        value = value['values']
    nodes = [ListNode(item) for item in cast(List[int], value or [])]
    for left, right in zip(nodes, nodes[1:]):
        left.next = right
    if nodes and cycle >= 0:
        nodes[-1].next = nodes[cycle]
    return nodes[0] if nodes else None


def list_nodes(head: Optional[ListNode]) -> List[ListNode]:
    nodes: List[ListNode] = []
    seen: Set[int] = set()
    while head is not None:
        if id(head) in seen:
            raise ValueError('Returned list contains a cycle.')
        seen.add(id(head))
        nodes.append(head)
        head = head.next
    return nodes


def normalize(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return {'$bigint': str(value)} if abs(value) > 9007199254740991 else value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('Return finite values.')
        return value
    if isinstance(value, ListNode):
        return [normalize(node.val) for node in list_nodes(value)]
    if isinstance(value, TreeNode):
        result: List[JsonValue] = []
        pending: Deque[Optional[TreeNode]] = deque([value])
        seen: Set[int] = set()
        while pending:
            node = pending.popleft()
            if node is None:
                result.append(None)
                continue
            if id(node) in seen:
                raise ValueError('Returned tree contains a cycle.')
            seen.add(id(node))
            result.append(normalize(node.val))
            pending.extend((node.left, node.right))
        while result and result[-1] is None:
            result.pop()
        return result
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in cast(Sequence[object], value)]
    if isinstance(value, dict):
        mapping = cast(Dict[object, object], value)
        if '$bigint' in mapping or '$json' in mapping:
            raise ValueError('JSON transport tags are reserved for the runner.')
        return {str(key): normalize(item) for key, item in mapping.items()}
    raise TypeError('Return a JSON value or a ListNode/TreeNode.')


def revive(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        if set(value) == {'$bigint'}:
            return int(cast(str, value['$bigint']))
        return {key: revive(item) for key, item in value.items()}
    if isinstance(value, list):
        return [revive(item) for item in value]
    return value


def argument(value: JsonValue, annotation: str) -> object:
    annotation = annotation.replace('typing.', '').replace(' ', '')
    if annotation.startswith('Optional['):
        return argument(value, annotation[9:-1])
    if annotation in ('TreeNode', 'TreeNode|None'):
        return tree_node(value)
    if annotation in ('ListNode', 'ListNode|None'):
        return list_node(value)
    if 'Node' in annotation and annotation.startswith(('List[', 'list[')):
        return [argument(item, annotation[5:-1]) for item in cast(List[JsonValue], value)]
    return value


def special_tree(root: Optional[TreeNode]) -> None:
    leaves: List[TreeNode] = []
    pending = [root] if root else []
    while pending:
        node = pending.pop()
        if node.left is None and node.right is None:
            leaves.append(node)
        else:
            if node.left:
                pending.append(node.left)
            if node.right:
                pending.append(node.right)
    leaves.sort(key=lambda node: node.val)
    for index, leaf in enumerate(leaves):
        leaf.left = leaves[index - 1]
        leaf.right = leaves[(index + 1) % len(leaves)]


class CapturedOutput(io.TextIOBase):
    def __init__(self) -> None:
        self.value = ''

    def write(self, text: str) -> int:
        self.value = (self.value + text)[:5000]
        return len(text)


def main() -> None:
    request = cast(Dict[str, object], json.load(sys.stdin))
    memory = cast(int, request['memoryLimitMb']) * 1024 * 1024
    cpu = cast(int, request['cpuLimitSeconds'])
    for limit, bounds in (
        (resource.RLIMIT_CPU, (cpu, cpu + 1)),
        (resource.RLIMIT_AS, (memory, memory)),
        (resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024)),
        (resource.RLIMIT_NPROC, (0, 0)),
    ):
        try:
            resource.setrlimit(limit, bounds)
        except (ValueError, OSError):
            pass
    common: Dict[str, object] = {}
    exec('''import collections, functools, itertools, heapq, bisect, math, random, re, string
from typing import *
from string import *
from re import *
from datetime import *
from collections import *
from heapq import *
from bisect import *
from copy import *
from math import *
from random import *
from statistics import *
from itertools import *
from functools import *
from operator import *
from io import *
from sys import *
from json import *
from builtins import *
from sortedcontainers import *
''', common)
    common.update({'TreeNode': TreeNode, 'ListNode': ListNode})
    inputs = cast(List[List[JsonValue]], request['inputs'])
    parameters = cast(List[Dict[str, str]], request['parameters'])
    entry_point = cast(str, request['entryPoint'])
    adapter = cast(Optional[str], request.get('adapter'))
    code = cast(str, request['code'])
    try:
        compiled = compile(code, 'submission.py', 'exec', flags=__future__.annotations.compiler_flag)
    except SyntaxError as error:
        sys.stdout.write(json.dumps({'error': 'Compilation error: ' + str(error)[:300]}) + '\n')
        return
    def timeout(_signum: int, _frame: object) -> None:
        raise TimeoutError('Testcase time limit exceeded.')

    signal.signal(signal.SIGALRM, timeout)
    for raw_input in inputs:
        stdout = CapturedOutput()
        stderr = CapturedOutput()
        record: Dict[str, str] = {}
        try:
            signal.setitimer(signal.ITIMER_REAL, 5)
            with contextlib.redirect_stdout(cast(TextIO, stdout)), contextlib.redirect_stderr(cast(TextIO, stderr)):
                args = [argument(revive(value), parameter['type']) for value, parameter in zip(raw_input, parameters)]
                if adapter == 'special-tree':
                    special_tree(cast(Optional[TreeNode], args[0]))
                original = list_nodes(cast(Optional[ListNode], args[0])) if adapter in ('middle-list', 'reuse-list') else []
                original_values = [node.val for node in original]
                namespace = dict(common)
                namespace.update({'stdout': stdout, 'stderr': stderr})
                exec(compiled, namespace)
                candidate = cast(Callable[..., object], eval(entry_point, namespace))
                actual = candidate(*args)
                return_type = cast(str, request['returnType'])
                if 'ListNode' in return_type and actual is not None and not isinstance(actual, ListNode):
                    raise TypeError('Return a ListNode.')
                if 'TreeNode' in return_type and actual is not None and not isinstance(actual, TreeNode):
                    raise TypeError('Return a TreeNode.')
                if adapter == 'prefix':
                    length = cast(int, actual)
                    actual = [length, cast(List[object], args[0])[:length]]
                elif adapter == 'middle-list':
                    expected_node = original[len(original) // 2] if original else None
                    if actual is not expected_node:
                        raise ValueError('Return the original middle node.')
                elif adapter == 'reuse-list':
                    k = cast(int, args[1])
                    expected_nodes: List[ListNode] = []
                    for index in range(0, len(original), k):
                        group = original[index:index + k]
                        expected_nodes.extend(reversed(group) if len(group) == k else group)
                    returned = list_nodes(cast(Optional[ListNode], actual))
                    if len(returned) != len(expected_nodes) or any(left is not right for left, right in zip(returned, expected_nodes)) \
                            or [node.val for node in original] != original_values:
                        raise ValueError('Reuse original nodes in the required group order without changing values.')
                elif adapter == 'mutated-tree' and normalize(args[0]) != normalize(actual):
                    raise ValueError('Mutate the original tree.')
                if actual is None and ('TreeNode' in return_type or 'ListNode' in return_type):
                    actual = []
                record['output'] = json.dumps(normalize(actual), ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        except Exception as error:
            record['error'] = 'Runtime error: ' + type(error).__name__ + ': ' + str(error)[:200]
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        record.update({'stdout': stdout.value, 'stderr': stderr.value})
        message = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
        if len(message.encode('utf-8')) > cast(int, request['outputLimit']):
            sys.stdout.write(json.dumps({'error': 'Output limit exceeded.'}) + '\n')
            return
        sys.stdout.write(message + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
