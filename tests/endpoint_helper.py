"""Pulls one function out of a source file to test it alone; no app startup runs."""
import ast
import asyncio
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]


def endpoint(filename, name, env):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    fn.decorator_list = []
    fn.returns = None
    for arg in fn.args.args: arg.annotation = None
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), filename, "exec"), env)
    return env[name]
