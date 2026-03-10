"""Sample project: intentionally imperfect code for AutoImprove to improve.

Has: no error handling, nested ifs, unused imports, no type hints, magic numbers.
"""

import os
import sys
import json
import random


def process_data(path):
    f = open(path)
    data = f.read()
    f.close()
    items = data.split("\n")
    result = []
    for item in items:
        if item != "":
            if len(item) > 3:
                if item[0] != "#":
                    if "error" not in item:
                        val = item.strip()
                        if val.isdigit():
                            if int(val) > 10:
                                if int(val) < 1000:
                                    result.append(int(val) * 2.5)
                                else:
                                    result.append(int(val))
                            else:
                                result.append(int(val))
                        else:
                            result.append(val)
    return result


def calculate_stats(numbers):
    total = 0
    count = 0
    for n in numbers:
        if type(n) == int or type(n) == float:
            total = total + n
            count = count + 1
    if count == 0:
        return {"avg": 0, "total": 0, "count": 0}
    avg = total / count
    return {"avg": avg, "total": total, "count": count}


def write_output(data, path):
    f = open(path, "w")
    for item in data:
        f.write(str(item) + "\n")
    f.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: main.py <input_file>")
        return
    input_path = sys.argv[1]
    output_path = sys.argv[1] + ".out"
    data = process_data(input_path)
    stats = calculate_stats(data)
    print("Stats:", stats)
    write_output(data, output_path)


if __name__ == "__main__":
    main()
