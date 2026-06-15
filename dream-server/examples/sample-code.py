#!/usr/bin/env python3
"""Sample code with intentional issues for code assistant demo."""

import json

def process_users(user_list):
    """Process a list of user dictionaries."""
    results = []
    for i in range(0, len(user_list)):
        user = user_list[i]
        # TODO: add validation
        name = user['name']
        email = user['email']
        age = user['age']

        if age > 18:
            status = 'adult'
        else:
            status = 'minor'

        results.append({
            'name': name,
            'email': email,
            'status': status
        })

    return results


def read_config(path):
    """Read configuration from JSON file."""
    f = open(path, 'r')
    data = json.load(f)
    return data


def calculate_average(numbers):
    """Calculate the average of a list of numbers."""
    total = 0
    for n in numbers:
        total = total + n
    avg = total / len(numbers)
    return avg


class DataProcessor:
    def __init__(self):
        self.data = []
        self.processed = False

    def load(self, items):
        for item in items:
            self.data.append(item)

    def process(self):
        new_data = []
        for d in self.data:
            new_data.append(d.upper())
        self.data = new_data
        self.processed = True

    def save(self, filename):
        with open(filename, 'w') as f:
            for item in self.data:
                f.write(item + '\n')


if __name__ == '__main__':
    users = [
        {'name': 'Alice', 'email': 'alice@example.com', 'age': 25},
        {'name': 'Bob', 'email': 'bob@example.com', 'age': 17},
    ]

    processed = process_users(users)
    print(processed)

    numbers = [1, 2, 3, 4, 5]
    avg = calculate_average(numbers)
    print(f'Average: {avg}')
