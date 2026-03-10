"""Utility functions — intentionally has lint issues, poor names, no docstrings."""

import os
import re


def proc(x):
    result = []
    for i in x:
        try:
            v = str(i).strip()
            if v:
                result.append(v)
        except:
            pass
    return result


def proc2(x):
    result = []
    for i in x:
        try:
            v = str(i).strip()
            if v:
                result.append(v)
        except:
            pass
    return result


def fmt(items, sep=","):
    unused_var = 42
    out = ""
    for i in range(len(items)):
        out = out + str(items[i])
        if i < len(items) - 1:
            out = out + sep
    return out


def chk(val):
    if val == None:
        return False
    if val == "":
        return False
    if val == 0:
        return False
    return True
