"""Parse the Linux kernel's system pressure-stall interface."""

import math


def parse_pressure(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        kind, *fields = line.split()
        if kind not in {'some', 'full'} or kind in result:
            raise ValueError('Invalid pressure row')
        values = dict(field.split('=', 1) for field in fields)
        if not {'avg10', 'avg60', 'avg300', 'total'} <= values.keys():
            raise ValueError('Missing pressure field')
        averages = {f'{key}_percent': float(values[key]) for key in ('avg10', 'avg60', 'avg300')}
        if any(not math.isfinite(value) or not 0 <= value <= 100 for value in averages.values()):
            raise ValueError('Invalid pressure average')
        total = int(values['total'])
        if total < 0:
            raise ValueError('Invalid pressure total')
        result[kind] = {**averages, 'total_us': total}
    if 'some' not in result:
        raise ValueError('Missing some pressure row')
    return result
